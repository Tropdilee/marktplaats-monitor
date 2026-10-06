"""Marktplaats search backend.

Uses the internal Marktplaats search API (/lrp/api/search) rather than scraping
the HTML page. That API does honour the filters the user sets in the app:

- sorting newest first (sortBy=SORT_INDEX, sortOrder=DECREASING)
- distance around a postcode (distanceMeters + postcode)
- maximum price (attributeRanges[]=PriceCents:0:N)
- more than 30 results through offset paging

Should the API be unreachable, the module falls back to reading the
__NEXT_DATA__ block from the ordinary search page. That yields the same data
structure, only unfiltered and capped at 30 results.
"""

import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests

from core.appinfo import USER_AGENT
from core.paths import data_file
from core.translations import tr

API_URL = "https://www.marktplaats.nl/lrp/api/search"
WEB_URL = "https://www.marktplaats.nl/q/{term}/"

# The API refuses a limit above 100; larger counts are fetched through offset.
MAX_API_LIMIT = 100
MAX_PAGES = 10

# Hard lower bound between two requests, whatever the app asks for. Marktplaats
# publishes no limit, so we stay well below what a person would do by hand.
# Without this floor a short interval or a bulk scan could fire a burst of
# requests within seconds and put needless load on the server.
MIN_SECONDS_BETWEEN_REQUESTS = 5.0

# Shortest interval between two monitoring cycles. The search form and the
# profile window both use this, so a profile cannot ask for less.
MIN_INTERVAL_SECONDS = 30

# Listings we have not seen in the results for longer than this are forgotten
# again. That keeps the memory file small without old listings coming back in
# as "new".
SEEN_RETENTION_SECONDS = 30 * 24 * 3600

# Paid promotion. Marktplaats resets such a listing's date to "Vandaag" every
# day, which keeps an old listing at the top of the newest-first ordering. For a
# search like "racefiets", 29 of the first 30 results are promotions, and a
# genuinely new listing never even enters the window.
PROMOTED_PRIORITY = "DAGTOPPER"

# priceType -> (translation key for the label, counts as a price for the
# maximum-price filter)
PRICE_TYPE_LABELS = {
    "FIXED": (None, True),
    "MIN_BID": ("price_bids_from", True),
    "FAST_BID": ("price_bid", False),
    "FREE": ("price_free", False),
    "RESERVED": ("price_reserved", False),
    "EXCHANGE": ("price_exchange", False),
    "NOTK": ("price_on_request", False),
    "ON_REQUEST": ("price_on_request", False),
    "SEE_DESCRIPTION": ("price_see_description", False),
}


class RateLimited(RuntimeError):
    """Marktplaats is holding requests back (429, or a 403 from the filter in front)."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class Cancelled(RuntimeError):
    """The search was cancelled, for instance because the window is closing."""


class MarktplaatsMonitor:
    def __init__(self, state_path=None):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "nl-NL,nl;q=0.9",
            }
        )
        self._last_request_at = 0.0

        # Set by cancel(); every wait in this class checks it, so a scan stops
        # within one request instead of running to its end.
        self._cancel = threading.Event()

        # Category list from the last response, so the app can show it without
        # making a separate request for it.
        self.last_category_options = []
        self.last_subcategories = []

        if state_path is None:
            state_path = data_file("seen_ids.json")
        self.state_path = Path(state_path)

        # {search term: {listing id: last seen (unix time)}}
        self._seen = self._load_state()

        # Becomes True when the last cycle was a first scan: everything was then
        # recorded as seen without sending any notifications.
        self.last_scan_was_priming = False

        # Becomes True when the search stopped because nothing usable came back
        # after filtering, for instance with a search full of promotions.
        self.last_search_exhausted = False

    # ------------------------------------------------------------------
    # Keeping track of which listings have already been seen
    # ------------------------------------------------------------------

    def _load_state(self):
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        cutoff = time.time() - SEEN_RETENTION_SECONDS
        state = {}
        for term, ids in raw.items():
            if not isinstance(ids, dict):
                continue
            # Kept even when empty: an empty entry still means "this search has
            # been scanned before". Dropping it would turn the next scan into a
            # silent first scan and swallow the first listing that ever appears.
            state[str(term)] = {
                str(k): float(v)
                for k, v in ids.items()
                if isinstance(v, (int, float)) and float(v) >= cutoff
            }
        return state

    def _save_state(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._seen, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self.state_path)
        except OSError:
            # Failing to save must never crash the monitor; we simply carry on
            # with this session's in-memory state.
            pass

    @staticmethod
    def _state_key(
        term,
        category_id=None,
        subcategory_id=None,
        hide_promoted=True,
        region=None,
        distance_km=None,
        max_price=None,
        free_only=False,
    ):
        """Key under which seen listings are stored.

        Every filter that changes which listings come back belongs in it. The
        same term with a higher maximum price, a wider radius or another
        category is a different search: listings that were simply outside the
        old filter would otherwise show up as "new" and trigger alerts. A
        changed search starts with its own silent first scan instead.
        """
        key = " ".join(str(term or "").lower().split())
        if not hide_promoted:
            key += "|promo"
        if category_id:
            key += f"|c{int(category_id)}"
            if subcategory_id:
                key += f"|s{int(subcategory_id)}"
        if region and distance_km and int(distance_km) > 0:
            postcode = str(region).replace(" ", "").upper()
            key += f"|pc{postcode}:{int(distance_km)}"
        if free_only:
            key += "|free"
        elif max_price and float(max_price) > 0:
            key += f"|max{int(round(float(max_price) * 100))}"
        return key

    @property
    def seen_ids(self):
        """Every known listing id, across all search terms."""
        ids = set()
        for entry in self._seen.values():
            ids.update(entry.keys())
        return ids

    # ------------------------------------------------------------------
    # Building the search request
    # ------------------------------------------------------------------

    def build_search_params(
        self,
        term,
        max_price=None,
        limit=30,
        offset=0,
        region=None,
        distance_km=None,
        free_only=False,
        category_id=None,
        subcategory_id=None,
    ):
        params = {
            "query": term,
            "limit": int(limit),
            "offset": int(offset),
            # Newest listings first; without these two Marktplaats returns a
            # relevance ordering in which a fresh listing easily falls outside
            # the first page.
            "sortBy": "SORT_INDEX",
            "sortOrder": "DECREASING",
        }

        if free_only:
            # Saves a lot of fetching and discarding. The server also lets bids
            # starting at EUR 0 through here, so we filter on priceType after.
            params["attributeRanges[]"] = "PriceCents:0:0"
        elif max_price and float(max_price) > 0:
            cents = int(round(float(max_price) * 100))
            params["attributeRanges[]"] = f"PriceCents:0:{cents}"

        # The distance filter only works when postcode and radius travel together.
        if region and distance_km and int(distance_km) > 0:
            params["postcode"] = str(region).strip().replace(" ", "").upper()
            params["distanceMeters"] = int(distance_km) * 1000

        # Mind the spelling: the main category goes as a plain number, the
        # subcategory as a list. A subcategory without a main category is
        # ignored by Marktplaats.
        if category_id:
            params["l1CategoryId"] = int(category_id)
            if subcategory_id:
                params["l2CategoryIds[]"] = int(subcategory_id)

        return params

    def build_search_url(self, term, max_price=None, limit=None, region=None, distance_km=None):
        """Build the search URL as a visitor sees it in the browser.

        Used for the HTML fallback and to be able to show the user a clickable
        link.
        """
        url = WEB_URL.format(term=quote_plus(str(term)))

        query = []
        if max_price and float(max_price) > 0:
            query.append(f"priceTo={int(float(max_price))}")
        if limit and int(limit) > 0:
            query.append(f"limit={int(limit)}")
        if query:
            url += "?" + "&".join(query)

        # Marktplaats itself puts the distance filter in the URL fragment. Note
        # that a fragment is never sent to the server, so this is purely for
        # display in the browser - the filter itself goes through the API.
        if region and distance_km and int(distance_km) > 0:
            url += (
                f"#distanceMeters:{int(distance_km) * 1000}"
                f"|postcode:{quote_plus(str(region))}"
            )

        return url

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def cancel(self):
        """Stop a running scan at the next opportunity. Safe from any thread."""
        self._cancel.set()

    def _pause(self, seconds):
        """Sleep, but wake up and stop as soon as the scan is cancelled."""
        if self._cancel.wait(max(0.0, seconds)):
            raise Cancelled()

    def _throttle(self):
        """Wait, if needed, until another request may be made."""
        self._pause(
            MIN_SECONDS_BETWEEN_REQUESTS - (time.monotonic() - self._last_request_at)
        )
        self._last_request_at = time.monotonic()

    @staticmethod
    def _retry_after_seconds(resp):
        try:
            return max(0, int(resp.headers.get("Retry-After", "")))
        except (TypeError, ValueError):
            return None

    def _request(self, params, attempts=3):
        delay = 5
        last_error = None

        for attempt in range(attempts):
            self._throttle()
            try:
                resp = self.session.get(API_URL, params=params, timeout=25)
            except requests.RequestException as exc:
                last_error = exc
            else:
                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code in (403, 429):
                    # Do not keep retrying here: that only makes it worse.
                    raise RateLimited(
                        tr("rate_limited").format(status=resp.status_code),
                        retry_after=self._retry_after_seconds(resp),
                    )

                if resp.status_code not in (500, 502, 503, 504):
                    resp.raise_for_status()

                last_error = requests.HTTPError(
                    tr("http_error").format(status=resp.status_code), response=resp
                )

            if attempt < attempts - 1:
                self._pause(delay)
                delay *= 2

        raise last_error

    def fetch_listings(
        self,
        term,
        max_price=None,
        limit=50,
        region=None,
        distance_km=None,
        free_only=False,
        category_id=None,
        subcategory_id=None,
        hide_promoted=True,
    ):
        wanted = max(1, int(limit or 50))
        collected = []
        seen_ids = set()
        empty_pages = 0
        self.last_search_exhausted = False

        try:
            offset = 0
            for _ in range(MAX_PAGES):
                # Always request a full page, even when only a few results are
                # still needed. One request of 100 costs the same as a request
                # of 30, but saves pages - and each page costs about 5 seconds
                # because of the wait between requests. With a strict filter
                # such as "free only" that is the difference between eight
                # rounds and three.
                page_size = MAX_API_LIMIT
                params = self.build_search_params(
                    term,
                    max_price,
                    page_size,
                    offset,
                    region,
                    distance_km,
                    free_only,
                    category_id,
                    subcategory_id,
                )
                data = self._request(params)
                self._remember_categories(data)
                listings = data.get("listings") or []

                before = len(collected)
                self._normalize_page(
                    listings, max_price, free_only, wanted, seen_ids, collected,
                    hide_promoted,
                )
                offset += len(listings)

                if len(collected) >= wanted or len(listings) < page_size:
                    break

                # Some searches are full of promotions hundreds of results deep
                # ("auto", for example). Paging on to MAX_PAGES then costs ten
                # requests without a single usable result. Two empty pages in a
                # row is enough evidence that searching further yields nothing.
                empty_pages = empty_pages + 1 if len(collected) == before else 0
                if empty_pages >= 2:
                    self.last_search_exhausted = True
                    break
                # Go easy on the server during large bulk scans.
                self._pause(0.5)
        except (RateLimited, Cancelled):
            # A block must reach the window so the monitor stops. Trying the
            # web page next would send a second request straight into it.
            raise
        except Exception:
            if collected or not self._fallback_can_honour(
                region, distance_km, category_id
            ):
                raise
            listings = self._fetch_listings_html(term, max_price, wanted)
            self._normalize_page(
                listings, max_price, free_only, wanted, seen_ids, collected,
                hide_promoted,
            )

        return collected

    def _remember_categories(self, data):
        """Pick the category lists out of a search response.

        Saves a separate request: the main categories and the subcategories
        belonging to this search term are already in the response.
        """
        options = data.get("searchCategoryOptions")
        if isinstance(options, list) and options:
            self.last_category_options = options

        subcategories = []
        for facet in data.get("facets") or []:
            if not isinstance(facet, dict) or facet.get("type") != "CategoryTreeFacet":
                continue
            for cat in facet.get("categories") or []:
                # Real subcategories only; the main category sits among them
                # without a parentId.
                if isinstance(cat, dict) and cat.get("parentId") and cat.get("label"):
                    subcategories.append(
                        {
                            "id": int(cat["id"]),
                            "name": str(cat["label"]),
                            "count": cat.get("histogramCount"),
                            "parent_id": int(cat["parentId"]),
                        }
                    )
        self.last_subcategories = subcategories

    @staticmethod
    def _fallback_can_honour(region, distance_km, category_id):
        """Whether the web-page fallback would return what was asked for.

        That page ignores every search argument. Price, free-only and the
        promotion filter are applied afterwards anyway, but a location or a
        category cannot be. Results from the whole country would then be stored
        under the filtered search and could trigger alerts for listings far
        outside the radius, so in that case it is better to report the error.
        """
        uses_location = bool(region and distance_km and int(distance_km) > 0)
        return not uses_location and not category_id

    def _fetch_listings_html(self, term, max_price, limit):
        """Fallback: read the listings from the web page's __NEXT_DATA__ block."""
        url = self.build_search_url(term, max_price, limit)
        self._throttle()
        resp = self.session.get(url, timeout=25, headers={"Accept": "text/html"})
        if resp.status_code in (403, 429):
            raise RateLimited(
                tr("rate_limited").format(status=resp.status_code),
                retry_after=self._retry_after_seconds(resp),
            )
        resp.raise_for_status()

        match = re.search(r'__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
        except ValueError:
            return []

        node = data.get("props", {}).get("pageProps", {}).get("searchRequestAndResponse")
        if isinstance(node, dict):
            return node.get("listings") or []
        return []

    # ------------------------------------------------------------------
    # Normalising
    # ------------------------------------------------------------------

    def _normalize_page(
        self, raw_items, max_price, free_only, limit, seen_ids, out, hide_promoted=True
    ):
        """Convert one page of API results and append them to `out`.

        `seen_ids` carries across pages, so a listing appearing on two pages ends
        up in the list only once.
        """
        for raw in raw_items:
            if len(out) >= limit:
                break

            if not isinstance(raw, dict):
                continue

            item_id = str(raw.get("itemId") or "").strip()
            if not item_id or item_id in seen_ids:
                continue

            # Listing ids starting with 'a' are Admarkt ads from external web
            # shops. They are often not even in the category being searched.
            if item_id.startswith("a"):
                continue

            # Otherwise promotions fill the entire window with listings that
            # are already days old, so nothing new ever comes into view.
            if hide_promoted and raw.get("priorityProduct") == PROMOTED_PRIORITY:
                continue

            price_value, price_text, price_type = self._read_price(raw.get("priceInfo"))

            if free_only and price_type != "FREE":
                continue

            countable = PRICE_TYPE_LABELS.get(price_type, (None, False))[1]
            if (
                max_price
                and float(max_price) > 0
                and countable
                and price_value is not None
                and price_value > float(max_price)
            ):
                continue

            seen_ids.add(item_id)
            out.append(
                {
                    "id": item_id,
                    "title": self._clean_text(raw.get("title") or tr("unknown")),
                    "price": price_text,
                    "price_value": price_value,
                    "price_type": price_type,
                    "location": self._read_location(raw.get("location")),
                    "time": self._clean_text(raw.get("date") or tr("unknown")),
                    "seller": self._read_seller(raw.get("sellerInformation")),
                    "description": self._clean_text(raw.get("description") or ""),
                    "image": self._read_image(raw.get("imageUrls")),
                    "promoted": raw.get("priorityProduct") == PROMOTED_PRIORITY,
                    "url": self._read_url(raw.get("vipUrl"), item_id),
                }
            )

        return out

    @staticmethod
    def _read_price(price_info):
        if not isinstance(price_info, dict):
            return None, tr("see_listing"), "UNKNOWN"

        price_type = str(price_info.get("priceType") or "UNKNOWN").upper()
        label_key, countable = PRICE_TYPE_LABELS.get(price_type, (None, False))
        label = tr(label_key) if label_key else None

        cents = price_info.get("priceCents")
        value = None
        if cents not in (None, ""):
            try:
                value = int(cents) / 100
            except (TypeError, ValueError):
                value = None

        if price_type == "FREE":
            return 0.0, tr("price_free"), price_type

        # A bid starting at EUR 0 is not a guide price, just "make an offer".
        if price_type == "MIN_BID" and not value:
            return None, tr("price_bid"), price_type

        if countable and value is not None:
            text = MarktplaatsMonitor._format_euro(value)
            if label:
                text = f"{text} ({label})"
            return value, text, price_type

        # Bidding without a guide price, price on request, and so on: no usable
        # amount, so price_value stays empty and the maximum-price filter skips
        # this entry.
        return None, label or tr("see_listing"), price_type

    @staticmethod
    def _format_euro(value):
        text = f"{value:,.2f}"
        text = text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
        return f"€ {text}"

    @staticmethod
    def _read_location(location):
        if isinstance(location, dict):
            for key in ("cityName", "name", "postalCode", "countryName"):
                if location.get(key):
                    return MarktplaatsMonitor._clean_text(str(location[key]))
        elif isinstance(location, str) and location.strip():
            return MarktplaatsMonitor._clean_text(location)
        return tr("unknown")

    @staticmethod
    def _read_seller(seller):
        if isinstance(seller, dict) and seller.get("sellerName"):
            return MarktplaatsMonitor._clean_text(str(seller["sellerName"]))
        return ""

    @staticmethod
    def _read_image(image_urls):
        if isinstance(image_urls, list) and image_urls:
            url = str(image_urls[0])
            if url.startswith("//"):
                return "https:" + url
            return url
        return ""

    @staticmethod
    def _read_url(vip_url, item_id):
        if vip_url:
            vip_url = str(vip_url)
            if vip_url.startswith("http"):
                return vip_url
            return "https://www.marktplaats.nl" + vip_url
        return f"https://link.marktplaats.nl/{item_id}"

    @staticmethod
    def _clean_text(text):
        return re.sub(r"\s+", " ", str(text)).strip()

    # ------------------------------------------------------------------
    # Determining which listings are new
    # ------------------------------------------------------------------

    def get_new_items(
        self,
        term,
        max_price=None,
        limit=50,
        region=None,
        distance_km=None,
        free_only=False,
        category_id=None,
        subcategory_id=None,
        hide_promoted=True,
    ):
        """Fetch results and work out which of them are new.

        The very first scan of a search term deliberately yields no new items:
        whatever is listed at that moment was not "just posted" after all.
        Without that step every restart of the app would produce a notification
        per result. Whether this happened is recorded in `last_scan_was_priming`.
        """
        items = self.fetch_listings(
            term,
            max_price=max_price,
            limit=limit,
            region=region,
            distance_km=distance_km,
            free_only=free_only,
            category_id=category_id,
            subcategory_id=subcategory_id,
            hide_promoted=hide_promoted,
        )

        key = self._state_key(
            term,
            category_id=category_id,
            subcategory_id=subcategory_id,
            hide_promoted=hide_promoted,
            region=region,
            distance_km=distance_km,
            max_price=max_price,
            free_only=free_only,
        )
        # Whether the key exists, not whether it holds anything. A search that
        # came back empty the first time has still been scanned; its first
        # listing ever must be reported, not quietly marked as seen.
        first_scan = key not in self._seen
        known = self._seen.setdefault(key, {})

        now = time.time()
        new_items = []
        for item in items:
            if item["id"] not in known:
                new_items.append(item)
            known[item["id"]] = now

        self.last_scan_was_priming = first_scan
        self._save_state()

        if first_scan:
            return [], items
        return new_items, items
