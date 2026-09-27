"""Offline tests for fetching pageviews. Run: python3 -m unittest discover -s tests"""

import json
import sys
import tempfile
import unittest
import urllib.parse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from wikiinterest import fetch as fetch_mod  # noqa: E402
from wikiinterest.fetch import _to_daily, _ttl, fetch, resolve_period  # noqa: E402


class FakeClient:
    """Wikidata entity + per-wiki page info + pageviews, all from fixtures."""

    def __init__(self, sitelinks, pages, views):
        self.sitelinks, self.pages, self.views = sitelinks, pages, views
        self.stats = {"network": 0, "cache": 0}
        self.urls = []

    def get_json(self, url, params=None, ttl=None):
        self.urls.append(url)
        if "wikidata" in url:
            links = {f"{l}wiki": {"title": t} for l, t in self.sitelinks.items()}
            return {"entities": {params["ids"]: {"labels": {"en": {"value": "Topic"}}, "sitelinks": links}}}
        if "api.php" in url:
            lang = url.split("//")[1].split(".")[0]
            return {"query": {"pages": [self.pages.get((lang, params["titles"]), {"title": params["titles"]})]}}
        # .../per-article/{lang}.wikipedia.org/all-access/{agent}/{title}/daily/... or .../aggregate/...
        parts = url.split("/metrics/pageviews/")[1].split("/")
        lang = parts[1].split(".")[0]
        key = (lang, "PROJECT") if parts[0] == "aggregate" else (lang, parts[3], urllib.parse.unquote(parts[4]))
        items = self.views.get(key)
        return None if items is None else {"items": [{"timestamp": d + "00", "views": v} for d, v in items.items()]}


def run(client, **kwargs):
    with tempfile.TemporaryDirectory() as tmp:
        result = fetch(client=client, start="2025-01", end="2025-01", out_dir=tmp, **kwargs)
        dataset = json.loads(Path(result["dataset"]).read_text()) if result["dataset"] else None
    return result, dataset


class PeriodTest(unittest.TestCase):
    def test_default_is_last_complete_months(self):
        self.assertEqual(resolve_period(months=24, today=date(2026, 9, 27)),
                         (date(2024, 9, 1), date(2026, 8, 31)))

    def test_explicit_months(self):
        self.assertEqual(resolve_period("2024-02", "2024-02", today=date(2026, 1, 1)),
                         (date(2024, 2, 1), date(2024, 2, 29)))

    def test_rejects_incomplete_or_too_early_months(self):
        with self.assertRaises(ValueError):
            resolve_period(end="2026-09", today=date(2026, 9, 27))
        with self.assertRaises(ValueError):
            resolve_period(start="2014-01", end="2015-12", today=date(2026, 1, 1))
        with self.assertRaises(ValueError):
            resolve_period(start="2025-13", today=date(2026, 1, 1))


class HelpersTest(unittest.TestCase):
    def test_missing_days_are_zero(self):
        days = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
        items = [{"timestamp": "2025010100", "views": 5}, {"timestamp": "2025010300", "views": 7}]
        self.assertEqual(_to_daily(items, days), [5, 0, 7])

    def test_old_data_cached_forever_recent_data_expires(self):
        self.assertIsNone(_ttl(date(2025, 1, 31), today=date(2025, 3, 1)))
        self.assertEqual(_ttl(date(2025, 2, 28), today=date(2025, 3, 1)), fetch_mod.DAY)


class FetchTest(unittest.TestCase):
    def test_sums_redirects_and_collects_all_series(self):
        client = FakeClient(
            sitelinks={"uk": "Астрономія"},
            pages={("uk", "Астрономія"): {"title": "Астрономія", "redirects": [{"title": "Astronomy"}]}},
            views={
                ("uk", "user", "Астрономія"): {"20250101": 10, "20250102": 20},
                ("uk", "user", "Astronomy"): {"20250102": 1},
                ("uk", "automated", "Астрономія"): {"20250101": 5},
                ("uk", "PROJECT"): {"20250101": 1000},
            })
        result, data = run(client, qid="Q333", langs=["uk"])
        s = data["series"]["uk"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(data["days"]), 31)
        self.assertEqual(s["article"][:3], [10, 20, 0])
        self.assertEqual(s["redirects"][:3], [0, 1, 0])
        self.assertEqual(s["automated"][0], 5)
        self.assertEqual(s["project_total"][0], 1000)
        self.assertEqual(result["series"]["uk"]["views_total"], 31)
        self.assertEqual(result["series"]["uk"]["automated_share"], round(5 / 35, 3))
        self.assertEqual(data["filters"]["agent"], "user")

    def test_missing_language_is_reported_not_invented(self):
        client = FakeClient({"cs": "Přerušovaný půst"}, {}, {("cs", "user", "Přerušovaný půst"): {"20250101": 3}})
        result, data = run(client, qid="Q1666254", langs=["pl", "cs"])
        self.assertEqual(result["status"], "partial")
        self.assertIn("pl", result["missing"])
        self.assertNotIn("pl", data["series"])
        self.assertIn("pl", result["agent_hint"])

    def test_proxy_article_is_marked(self):
        client = FakeClient({"cs": "Přerušovaný půst"}, {}, {})
        result, _ = run(client, qid="Q1666254", langs=["pl", "cs"], overrides={"pl": "Głodówka lecznicza"})
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["series"]["pl"]["proxy"])
        self.assertFalse(result["series"]["cs"]["proxy"])
        self.assertIn("proxy", result["dataset"])
        self.assertIn("proxy", result["agent_hint"])

    def test_disambiguation_page_is_not_fetched(self):
        client = FakeClient({"uk": "Меркурій"}, {("uk", "Меркурій"): {"title": "Меркурій", "pageprops": {"disambiguation": ""}}}, {})
        result, data = run(client, qid="Q1", langs=["uk"])
        self.assertEqual(result["status"], "no_data")
        self.assertIsNone(data)
        self.assertFalse(any("per-article" in u for u in client.urls))

    def test_redirects_are_capped(self):
        many = [{"title": f"R{i}"} for i in range(fetch_mod.MAX_REDIRECTS + 5)]
        client = FakeClient({"uk": "X"}, {("uk", "X"): {"title": "X", "redirects": many}}, {})
        result, _ = run(client, qid="Q1", langs=["uk"])
        self.assertEqual(result["series"]["uk"]["redirects_included"], fetch_mod.MAX_REDIRECTS)
        self.assertEqual(result["series"]["uk"]["redirects_skipped"], 5)

    def test_argument_validation(self):
        client = FakeClient({}, {}, {})
        with self.assertRaises(ValueError):
            fetch(langs=["uk"], client=client)  # neither qid nor article
        with self.assertRaises(ValueError):
            fetch(langs=["uk"], overrides={"pl": "X"}, client=client)  # override for a language not requested


if __name__ == "__main__":
    unittest.main()
