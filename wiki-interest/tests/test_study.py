"""Offline tests for the one-call `study` command. Run: python3 -m unittest discover -s tests"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_fetch import FakeClient as FetchFake  # noqa: E402
from test_resolve import FakeClient as ResolveFake, entity, many_wikis  # noqa: E402
from wikiinterest.study import study  # noqa: E402


class StudyFake(FetchFake):
    """Fetch fixtures plus a Wikidata search that returns one QID."""

    def get_json(self, url, params=None, ttl=None):
        if params and params.get("action") == "wbsearchentities":
            return {"search": [{"id": "Q1"}]}
        if params and params.get("list") == "search":
            return {"query": {"search": [{"title": "Related article", "snippet": "…"}]}}
        return super().get_json(url, params, ttl)


def views(lang, title):
    return {(lang, "user", title): {"20250115": 100}, (lang, "PROJECT"): {"20250115": 1_000_000}}


class StudyTest(unittest.TestCase):
    def run_study(self, client, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            return study(client=client, start="2025-01", end="2025-01", out_dir=tmp, **kw)

    def test_stops_for_the_user_when_topic_is_ambiguous(self):
        entities = {"Q308": entity({"uk": "Меркурій"}, many_wikis(250, ukwiki="Меркурій (планета)")),
                    "Q1150": entity({"uk": "Меркурій"}, many_wikis(84, ukwiki="Меркурій (міфологія)"))}
        r = self.run_study(ResolveFake({("Меркурій", "uk"): ["Q308", "Q1150"]}, entities),
                           topic="Меркурій", langs=["uk"])
        self.assertEqual(r["status"], "needs_user")
        self.assertEqual(r["resolve"]["alternatives"][0]["qid"], "Q1150")
        self.assertNotIn("analysis", r)  # nothing fetched before the user decides

    def test_full_path_with_a_missing_language(self):
        client = StudyFake({"cs": "Téma"}, {}, views("cs", "Téma"))
        r = self.run_study(client, topic="topic", langs=["pl", "cs"], search_lang="en")
        self.assertEqual(r["status"], "partial")
        self.assertIn("cs", r["results"])
        self.assertEqual(r["missing"]["pl"]["search_hits"][0]["title"], "Related article")
        self.assertTrue(r["facts"])
        self.assertIn("proxy", r["agent_hint"])

    def test_qid_skips_topic_search(self):
        client = StudyFake({"uk": "Тема"}, {}, views("uk", "Тема"))
        r = self.run_study(client, qid="Q1", langs=["uk"])
        self.assertEqual(r["status"], "ok")
        self.assertFalse(any("wbsearchentities" in u for u in client.urls))


if __name__ == "__main__":
    unittest.main()
