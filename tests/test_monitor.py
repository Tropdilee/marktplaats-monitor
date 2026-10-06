"""Tests for the search backend. No network: every request is faked."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import requests

from core.monitor import Cancelled, MarktplaatsMonitor, RateLimited
from core.translations import set_language


def fake_response(status, body=b"", content_type="application/json"):
    resp = requests.Response()
    resp.status_code = status
    resp._content = body if isinstance(body, bytes) else body.encode("utf-8")
    resp.headers["Content-Type"] = content_type
    resp.encoding = "utf-8"
    resp.url = "https://example.invalid/"
    return resp


def listing(item_id, price_type="FIXED", cents=1000, priority="NONE"):
    return {
        "itemId": item_id,
        "title": f"Listing {item_id}",
        "priceInfo": {"priceType": price_type, "priceCents": cents},
        "priorityProduct": priority,
        "location": {"cityName": "Zwolle"},
        "date": "Vandaag",
        "vipUrl": f"/v/x/{item_id}",
    }


def api_page(*items):
    return fake_response(200, json.dumps({"listings": list(items)}))


def html_page(*items):
    data = {"props": {"pageProps": {"searchRequestAndResponse": {"listings": list(items)}}}}
    body = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    return fake_response(200, body, "text/html")


class MonitorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.monitor = self.make_monitor()
        set_language("English")

    def tearDown(self):
        set_language("Nederlands")

    def make_monitor(self):
        monitor = MarktplaatsMonitor(state_path=self.tmp / "seen.json")
        # The real waits are up to 20 seconds; the tests only care about order.
        monitor._pause = lambda seconds: None
        return monitor

    def serve(self, *responses):
        """Answer requests in order; returns the list of URLs requested."""
        queue = list(responses)
        requested = []

        def get(url, **kwargs):
            requested.append(url)
            if not queue:
                raise AssertionError(f"unexpected extra request #{len(requested)} to {url}")
            reply = queue.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        patcher = mock.patch.object(self.monitor.session, "get", side_effect=get)
        patcher.start()
        self.addCleanup(patcher.stop)
        return requested


class RateLimitTests(MonitorTestCase):
    def test_block_on_first_request_reaches_the_caller(self):
        requested = self.serve(fake_response(403))
        with self.assertRaises(RateLimited):
            self.monitor.fetch_listings("fiets", limit=5)
        self.assertEqual(len(requested), 1, "no second request into the block")

    def test_429_is_a_block_too(self):
        self.serve(fake_response(429))
        with self.assertRaises(RateLimited):
            self.monitor.fetch_listings("fiets", limit=5)

    def test_block_on_the_fallback_page_is_reported_as_a_block(self):
        self.serve(
            requests.ConnectionError(), requests.ConnectionError(),
            requests.ConnectionError(), fake_response(403, "", "text/html"),
        )
        with self.assertRaises(RateLimited):
            self.monitor.fetch_listings("fiets", limit=5)

    def test_message_follows_the_language(self):
        self.serve(fake_response(429))
        with self.assertRaises(RateLimited) as caught:
            self.monitor.fetch_listings("fiets", limit=5)
        self.assertIn("blocking requests", str(caught.exception))


class FallbackTests(MonitorTestCase):
    def test_fallback_used_when_it_can_honour_the_search(self):
        self.serve(
            requests.ConnectionError(), requests.ConnectionError(),
            requests.ConnectionError(), html_page(listing("m1")),
        )
        items = self.monitor.fetch_listings("fiets", limit=5)
        self.assertEqual([i["id"] for i in items], ["m1"])

    def test_no_fallback_with_a_location_filter(self):
        requested = self.serve(*[requests.ConnectionError()] * 3)
        with self.assertRaises(requests.ConnectionError):
            self.monitor.fetch_listings("fiets", limit=5, region="8022RT", distance_km=15)
        self.assertEqual(len(requested), 3, "the unfiltered web page must not be tried")

    def test_no_fallback_with_a_category(self):
        requested = self.serve(*[requests.ConnectionError()] * 3)
        with self.assertRaises(requests.ConnectionError):
            self.monitor.fetch_listings("fiets", limit=5, category_id=445)
        self.assertEqual(len(requested), 3)


class FirstScanTests(MonitorTestCase):
    def test_search_that_starts_empty_reports_its_first_listing(self):
        self.serve(api_page(), api_page(listing("m1")))
        new, _ = self.monitor.get_new_items("zeldzaam", limit=5)
        self.assertEqual(new, [])
        self.assertTrue(self.monitor.last_scan_was_priming)

        new, _ = self.monitor.get_new_items("zeldzaam", limit=5)
        self.assertEqual([i["id"] for i in new], ["m1"])
        self.assertFalse(self.monitor.last_scan_was_priming)

    def test_empty_search_is_remembered_across_a_restart(self):
        self.serve(api_page())
        self.monitor.get_new_items("zeldzaam", limit=5)

        self.monitor = self.make_monitor()  # reads the state file again
        self.serve(api_page(listing("m1")))
        new, _ = self.monitor.get_new_items("zeldzaam", limit=5)
        self.assertEqual([i["id"] for i in new], ["m1"])

    def test_first_scan_of_a_search_stays_silent(self):
        self.serve(api_page(listing("m1"), listing("m2")))
        new, items = self.monitor.get_new_items("fiets", limit=5)
        self.assertEqual(new, [])
        self.assertEqual(len(items), 2)


class StateKeyTests(unittest.TestCase):
    key = staticmethod(MarktplaatsMonitor._state_key)

    def test_every_filter_changes_the_key(self):
        base = self.key("fiets")
        for variant in (
            self.key("fiets", max_price=400),
            self.key("fiets", region="8022RT", distance_km=15),
            self.key("fiets", free_only=True),
            self.key("fiets", category_id=445),
            self.key("fiets", hide_promoted=False),
        ):
            self.assertNotEqual(base, variant)

    def test_raising_the_maximum_price_is_a_new_search(self):
        self.assertNotEqual(self.key("fiets", max_price=400), self.key("fiets", max_price=450))

    def test_formatting_of_term_and_postcode_does_not_matter(self):
        self.assertEqual(
            self.key("Race  Fiets", region="8022 rt", distance_km=15),
            self.key("race fiets", region="8022RT", distance_km=15),
        )

    def test_postcode_without_distance_is_no_filter(self):
        self.assertEqual(self.key("fiets", region="8022RT", distance_km=0), self.key("fiets"))


class CancelTests(unittest.TestCase):
    def test_cancel_interrupts_the_wait_between_requests(self):
        monitor = MarktplaatsMonitor(state_path=Path(tempfile.mkdtemp()) / "s.json")
        monitor._last_request_at = time.monotonic()  # next request must wait ~5 s
        threading.Timer(0.1, monitor.cancel).start()

        started = time.monotonic()
        with self.assertRaises(Cancelled):
            monitor._throttle()
        self.assertLess(time.monotonic() - started, 2.0)


class LabelTests(MonitorTestCase):
    def test_price_labels_follow_the_language(self):
        read = MarktplaatsMonitor._read_price
        set_language("English")
        self.assertEqual(read({"priceType": "FREE"})[1], "Free")
        self.assertIn("bids from", read({"priceType": "MIN_BID", "priceCents": 5000})[1])
        set_language("Nederlands")
        self.assertEqual(read({"priceType": "FREE"})[1], "Gratis")
        self.assertIn("bieden vanaf", read({"priceType": "MIN_BID", "priceCents": 5000})[1])


if __name__ == "__main__":
    unittest.main()
