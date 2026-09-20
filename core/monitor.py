"""Marktplaats zoek-backend.

Gebruikt de interne zoek-API van Marktplaats (/lrp/api/search) in plaats van het
scrapen van de HTML-pagina. Die API respecteert wel de filters die de gebruiker
in de app instelt:

- sortering op nieuwste eerst (sortBy=SORT_INDEX, sortOrder=DECREASING)
- afstand rondom een postcode (distanceMeters + postcode)
- maximale prijs (attributeRanges[]=PriceCents:0:N)
- meer dan 30 resultaten via offset-paginering

Als de API onverhoopt niet bereikbaar is, valt de module terug op het uitlezen
van het __NEXT_DATA__-blok van de gewone zoekpagina. Dat levert dezelfde
datastructuur op, alleen ongefilterd en maximaal 30 resultaten.
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests

from core.paths import data_file

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36"
)

API_URL = "https://www.marktplaats.nl/lrp/api/search"
WEB_URL = "https://www.marktplaats.nl/q/{term}/"

# De API weigert een limit boven de 100; grotere aantallen halen we op via offset.
MAX_API_LIMIT = 100
MAX_PAGES = 10

# Harde ondergrens tussen twee verzoeken, ongeacht wat de app vraagt. Marktplaats
# publiceert geen limiet, dus we blijven ruim onder wat een mens met de hand zou
# doen. Zonder deze vloer kan een korte interval of een bulk-scan een reeks
# verzoeken in een paar seconden wegsturen, en dat is precies wat een
# beveiligingsfilter eruit pikt.
MIN_SECONDS_BETWEEN_REQUESTS = 5.0

# Advertenties die we langer dan dit niet meer in de resultaten zien, vergeten we
# weer. Zo blijft het geheugenbestand klein zonder dat oude advertenties opnieuw
# als "nieuw" binnenkomen.
SEEN_RETENTION_SECONDS = 30 * 24 * 3600

# Betaalde promotie. Marktplaats zet de datum van zo'n advertentie elke dag
# weer op "Vandaag", waardoor een oude advertentie bovenaan de sortering op
# nieuwste blijft staan. Bij een zoekterm als "racefiets" zijn 29 van de eerste
# 30 resultaten promoties, en dan komt een echt nieuwe advertentie het venster
# niet eens in.
PROMOTED_PRIORITY = "DAGTOPPER"

# priceType -> (label, telt als prijs voor het maximum-prijsfilter)
PRICE_TYPE_LABELS = {
    "FIXED": (None, True),
    "MIN_BID": ("bieden vanaf", True),
    "FAST_BID": ("Bieden", False),
    "FREE": ("Gratis", False),
    "RESERVED": ("Gereserveerd", False),
    "EXCHANGE": ("Ruilen", False),
    "NOTK": ("Op aanvraag", False),
    "ON_REQUEST": ("Op aanvraag", False),
    "SEE_DESCRIPTION": ("Zie omschrijving", False),
}


class RateLimited(RuntimeError):
    """Marktplaats houdt de verzoeken tegen (429, of een 403 van het filter ervoor)."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class MarktplaatsMonitor:
    def __init__(self, state_path=None):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "nl-NL,nl;q=0.9",
                "Referer": "https://www.marktplaats.nl/",
            }
        )
        self._last_request_at = 0.0

        # Categorielijst uit het laatste antwoord, zodat de app die kan tonen
        # zonder er een apart verzoek voor te doen.
        self.last_category_options = []
        self.last_subcategories = []

        if state_path is None:
            state_path = data_file("seen_ids.json")
        self.state_path = Path(state_path)

        # {zoekterm: {advertentie-id: laatst gezien (unix-tijd)}}
        self._seen = self._load_state()

        # Wordt True als de laatste cyclus een eerste scan was: dan is alles als
        # gezien weggeschreven zonder meldingen te sturen.
        self.last_scan_was_priming = False

        # Wordt True als het zoeken gestopt is omdat er na het filteren niets
        # bruikbaars meer kwam, bijvoorbeeld bij een zoekterm vol promoties.
        self.last_search_exhausted = False

    # ------------------------------------------------------------------
    # Opslag van welke advertenties al gezien zijn
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
            fresh = {
                str(k): float(v)
                for k, v in ids.items()
                if isinstance(v, (int, float)) and float(v) >= cutoff
            }
            if fresh:
                state[str(term)] = fresh
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
            # Niet kunnen opslaan mag de monitor nooit laten crashen; we draaien
            # dan simpelweg verder op het geheugen van deze sessie.
            pass

    @staticmethod
    def _state_key(term, category_id=None, subcategory_id=None, hide_promoted=True):
        """Sleutel waaronder gezien-advertenties bewaard worden.

        De categorie hoort erbij: dezelfde zoekterm in een andere categorie is
        een andere zoekopdracht, en die begint dus met een eigen eerste scan in
        plaats van met een golf meldingen.
        """
        key = " ".join(str(term or "").lower().split())
        if not hide_promoted:
            # Promoties aanzetten laat advertenties zien die nog nooit in beeld
            # zijn geweest. Een eigen sleutel zorgt dat die niet in één klap als
            # "nieuw" de deur uit gaan.
            key += "|promo"
        if category_id:
            key += f"|c{int(category_id)}"
            if subcategory_id:
                key += f"|s{int(subcategory_id)}"
        return key

    def has_seen_term(self, term, category_id=None, subcategory_id=None, hide_promoted=True):
        """True als deze zoekopdracht eerder gescand is."""
        return bool(
            self._seen.get(self._state_key(term, category_id, subcategory_id, hide_promoted))
        )

    def forget_term(self, term, category_id=None, subcategory_id=None, hide_promoted=True):
        """Vergeet de geschiedenis van één zoekopdracht; de volgende scan begint schoon."""
        self._seen.pop(self._state_key(term, category_id, subcategory_id, hide_promoted), None)
        self._save_state()

    @property
    def seen_ids(self):
        """Alle bekende advertentie-ids, over alle zoektermen heen."""
        ids = set()
        for entry in self._seen.values():
            ids.update(entry.keys())
        return ids

    # ------------------------------------------------------------------
    # Opbouwen van de zoekopdracht
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
            # Nieuwste advertenties eerst; zonder deze twee komt Marktplaats met
            # een relevantie-sortering waarin een verse advertentie makkelijk
            # buiten de eerste pagina valt.
            "sortBy": "SORT_INDEX",
            "sortOrder": "DECREASING",
        }

        if free_only:
            # Scheelt een hoop ophalen en weggooien. De server laat hier ook
            # biedingen vanaf € 0 door, dus we filteren daarna nog op priceType.
            params["attributeRanges[]"] = "PriceCents:0:0"
        elif max_price and float(max_price) > 0:
            cents = int(round(float(max_price) * 100))
            params["attributeRanges[]"] = f"PriceCents:0:{cents}"

        # Het afstandsfilter werkt alleen als postcode en straal samen meegaan.
        if region and distance_km and int(distance_km) > 0:
            params["postcode"] = str(region).strip().replace(" ", "").upper()
            params["distanceMeters"] = int(distance_km) * 1000

        # Let op de schrijfwijze: de hoofdcategorie gaat als los getal mee, de
        # subcategorie als lijst. Een subcategorie zonder hoofdcategorie wordt
        # door Marktplaats genegeerd.
        if category_id:
            params["l1CategoryId"] = int(category_id)
            if subcategory_id:
                params["l2CategoryIds[]"] = int(subcategory_id)

        return params

    def build_search_url(self, term, max_price=None, limit=None, region=None, distance_km=None):
        """Bouw de zoek-URL zoals een bezoeker die in de browser ziet.

        Wordt gebruikt voor de HTML-fallback en om de gebruiker een klikbare
        link te kunnen tonen.
        """
        url = WEB_URL.format(term=quote_plus(str(term)))

        query = []
        if max_price and float(max_price) > 0:
            query.append(f"priceTo={int(float(max_price))}")
        if limit and int(limit) > 0:
            query.append(f"limit={int(limit)}")
        if query:
            url += "?" + "&".join(query)

        # Marktplaats zet het afstandsfilter zelf in het URL-fragment. Let op:
        # een fragment wordt nooit naar de server gestuurd, dus dit is puur voor
        # weergave in de browser - het filter zelf gaat via de API.
        if region and distance_km and int(distance_km) > 0:
            url += (
                f"#distanceMeters:{int(distance_km) * 1000}"
                f"|postcode:{quote_plus(str(region))}"
            )

        return url

    # ------------------------------------------------------------------
    # Ophalen
    # ------------------------------------------------------------------

    def _throttle(self):
        """Wacht zo nodig tot er weer een verzoek gedaan mag worden."""
        wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
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
                    # Hier niet blijven proberen: dat maakt het alleen erger.
                    raise RateLimited(
                        f"Marktplaats blokkeert de verzoeken (HTTP {resp.status_code}). "
                        "Zet de interval hoger en probeer het later opnieuw.",
                        retry_after=self._retry_after_seconds(resp),
                    )

                if resp.status_code not in (500, 502, 503, 504):
                    resp.raise_for_status()

                last_error = requests.HTTPError(
                    f"HTTP {resp.status_code} van Marktplaats", response=resp
                )

            if attempt < attempts - 1:
                time.sleep(delay)
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
                # Altijd een volle pagina opvragen, ook als er nog maar een
                # paar resultaten nodig zijn. Eén verzoek van 100 kost hetzelfde
                # als een verzoek van 30, maar scheelt pagina's - en elke pagina
                # kost door de wachttijd tussen verzoeken zo'n 5 seconden. Met
                # een streng filter zoals "alleen gratis" scheelt dat het
                # verschil tussen acht en drie ronden.
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

                # Sommige zoektermen staan honderden resultaten diep vol met
                # promoties ("auto" bijvoorbeeld). Dan blijft doorbladeren tot
                # MAX_PAGES tien verzoeken kosten zonder één bruikbaar
                # resultaat. Twee lege pagina's op rij is genoeg bewijs dat
                # verder zoeken niets meer oplevert.
                empty_pages = empty_pages + 1 if len(collected) == before else 0
                if empty_pages >= 2:
                    self.last_search_exhausted = True
                    break
                # Rustig aan tegen de server bij grote bulk-scans.
                time.sleep(0.5)
        except Exception:
            if collected:
                raise
            listings = self._fetch_listings_html(term, max_price, wanted)
            self._normalize_page(
                listings, max_price, free_only, wanted, seen_ids, collected,
                hide_promoted,
            )

        return collected

    def _remember_categories(self, data):
        """Pik de categorielijsten uit een zoekantwoord.

        Scheelt een apart verzoek: de hoofdcategorieën en de subcategorieën die
        bij deze zoekterm horen zitten al in het antwoord.
        """
        options = data.get("searchCategoryOptions")
        if isinstance(options, list) and options:
            self.last_category_options = options

        subcategories = []
        for facet in data.get("facets") or []:
            if not isinstance(facet, dict) or facet.get("type") != "CategoryTreeFacet":
                continue
            for cat in facet.get("categories") or []:
                # Alleen echte subcategorieën; de hoofdcategorie staat er zonder
                # parentId tussen.
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

    def _fetch_listings_html(self, term, max_price, limit):
        """Terugvaloptie: lees de listings uit het __NEXT_DATA__-blok van de webpagina."""
        url = self.build_search_url(term, max_price, limit)
        resp = self.session.get(url, timeout=25, headers={"Accept": "text/html"})
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
    # Normaliseren
    # ------------------------------------------------------------------

    def _normalize_page(
        self, raw_items, max_price, free_only, limit, seen_ids, out, hide_promoted=True
    ):
        """Zet één pagina API-resultaten om en hang ze achter `out`.

        `seen_ids` loopt over de pagina's heen mee, zodat een advertentie die op
        twee pagina's opduikt maar één keer in de lijst belandt.
        """
        for raw in raw_items:
            if len(out) >= limit:
                break

            if not isinstance(raw, dict):
                continue

            item_id = str(raw.get("itemId") or "").strip()
            if not item_id or item_id in seen_ids:
                continue

            # Advertentie-ids die met 'a' beginnen zijn Admarkt-advertenties van
            # externe webshops. Die staan vaak niet eens in de gezochte categorie.
            if item_id.startswith("a"):
                continue

            # Promoties vullen anders het hele venster met advertenties die al
            # dagen bestaan, zodat er nooit iets nieuws in beeld komt.
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
                    "title": self._clean_text(raw.get("title") or "Onbekend"),
                    "price": price_text,
                    "price_value": price_value,
                    "price_type": price_type,
                    "location": self._read_location(raw.get("location")),
                    "time": self._clean_text(raw.get("date") or "Onbekend"),
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
            return None, "Zie advertentie", "UNKNOWN"

        price_type = str(price_info.get("priceType") or "UNKNOWN").upper()
        label, countable = PRICE_TYPE_LABELS.get(price_type, (None, False))

        cents = price_info.get("priceCents")
        value = None
        if cents not in (None, ""):
            try:
                value = int(cents) / 100
            except (TypeError, ValueError):
                value = None

        if price_type == "FREE":
            return 0.0, "Gratis", price_type

        # Een bod vanaf € 0 is geen richtprijs maar gewoon "bieden".
        if price_type == "MIN_BID" and not value:
            return None, "Bieden", price_type

        if countable and value is not None:
            text = MarktplaatsMonitor._format_euro(value)
            if label:
                text = f"{text} ({label})"
            return value, text, price_type

        # Bieden zonder richtprijs, n.o.t.k., enzovoort: geen bruikbaar bedrag,
        # dus price_value blijft leeg en het maximum-prijsfilter slaat dit over.
        return None, label or "Zie advertentie", price_type

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
        return "Onbekend"

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
    # Nieuwe advertenties bepalen
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
        """Haal resultaten op en bepaal welke daarvan nieuw zijn.

        De allereerste scan van een zoekterm levert bewust geen nieuwe items op:
        alles wat er op dat moment staat is immers niet "zojuist geplaatst".
        Zonder die stap zou elke herstart van de app een melding per resultaat
        opleveren. Of dit gebeurd is, staat in `last_scan_was_priming`.
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

        key = self._state_key(term, category_id, subcategory_id, hide_promoted)
        known = self._seen.get(key)
        first_scan = not known
        if known is None:
            known = {}
            self._seen[key] = known

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
