"""Offline tests for topic resolution. Run: python3 -m unittest discover -s tests"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from wikiinterest.resolve import resolve  # noqa: E402


def entity(labels, sitelinks, p31=(), descriptions=None):
    return {
        "labels": {l: {"value": v} for l, v in labels.items()},
        "descriptions": {l: {"value": v} for l, v in (descriptions or {}).items()},
        "sitelinks": {k: {"title": t} for k, t in sitelinks.items()},
        "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": c}}}} for c in p31]},
    }


def many_wikis(n, **explicit):
    links = {f"x{i}wiki": f"T{i}" for i in range(n)}
    links.update(explicit)
    return links


class FakeClient:
    """Answers the handful of API calls resolve() makes from in-memory fixtures."""

    def __init__(self, search, entities, pages=None, fulltext=None):
        self.search, self.entities = search, entities
        self.pages, self.fulltext = pages or {}, fulltext or {}
        self.stats = {"network": 0, "cache": 0}
        self.calls = []

    def get_json(self, url, params=None, ttl=None):
        self.calls.append((url, params))
        if "wikidata" in url and params["action"] == "wbsearchentities":
            return {"search": [{"id": q} for q in self.search.get((params["search"], params["language"]), [])]}
        if "wikidata" in url and params["action"] == "wbgetentities":
            return {"entities": {q: self.entities[q] for q in params["ids"].split("|") if q in self.entities}}
        lang = url.split("//")[1].split(".")[0]
        if params.get("list") == "search":
            return {"query": {"search": self.fulltext.get(lang, [])}}
        page = self.pages.get((lang, params["titles"]), {"title": params["titles"]})
        return {"query": {"pages": [page]}}


ASTRONOMY = {
    "Q333": entity({"uk": "астрономія", "en": "astronomy"},
                   many_wikis(200, ukwiki="Астрономія", plwiki="Astronomia", cswiki="Astronomie")),
    "Q12012641": entity({"en": "Astronomy"}, {"enwiki": "Astronomy (Harry Potter)"}),
    "Q21451142": entity({"uk": "властивість"}, {}),
    "Q999": entity({"uk": "Астрономія (значення)"}, {"ukwiki": "Астрономія (значення)"}, p31=["Q4167410"]),
}


class ResolveTest(unittest.TestCase):
    def test_picks_main_meaning_and_excludes_junk(self):
        client = FakeClient({("астрономія", "uk"): ["Q21451142", "Q12012641", "Q999", "Q333"]}, ASTRONOMY)
        r = resolve("астрономія", ["uk", "pl", "cs"], client=client)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["chosen"]["qid"], "Q333")
        self.assertEqual({l: a["title"] for l, a in r["articles"].items()},
                         {"uk": "Астрономія", "pl": "Astronomia", "cs": "Astronomie"})
        reasons = {e["qid"]: e["reason"] for e in r["excluded"]}
        self.assertEqual(reasons, {"Q21451142": "no Wikipedia article", "Q999": "disambiguation page"})

    def test_ambiguous_when_second_meaning_is_popular(self):
        entities = {
            "Q308": entity({"uk": "Меркурій"}, many_wikis(250, ukwiki="Меркурій (планета)")),
            "Q1150": entity({"uk": "Меркурій"}, many_wikis(84, ukwiki="Меркурій (міфологія)")),
        }
        r = resolve("Меркурій", ["uk"], client=FakeClient({("Меркурій", "uk"): ["Q1150", "Q308"]}, entities))
        self.assertEqual(r["status"], "ambiguous")
        self.assertEqual(r["chosen"]["qid"], "Q308")
        self.assertEqual(r["alternatives"][0]["qid"], "Q1150")
        self.assertIn("--qid", r["agent_hint"])

    def test_explicit_qid_is_never_ambiguous(self):
        entities = {"Q1150": entity({"uk": "Меркурій"}, many_wikis(84, ukwiki="Меркурій (міфологія)"))}
        r = resolve(qid="Q1150", langs=["uk"], client=FakeClient({}, entities))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["articles"]["uk"]["title"], "Меркурій (міфологія)")

    def test_missing_language_gets_search_hits_not_substitution(self):
        entities = {"Q1666254": entity({"en": "intermittent fasting"}, many_wikis(30, cswiki="Přerušovaný půst"))}
        client = FakeClient({("intermittent fasting", "en"): ["Q1666254"]}, entities,
                            fulltext={"pl": [{"title": "Głodówka lecznicza", "snippet": "<b>fasting</b>"}]})
        r = resolve("intermittent fasting", ["pl", "cs"], search_lang="en", client=client)
        self.assertEqual(r["articles"]["cs"]["status"], "ok")
        pl = r["articles"]["pl"]
        self.assertEqual(pl["status"], "no_article")
        self.assertEqual(pl["search_query"], "intermittent fasting")
        self.assertEqual(pl["search_hits"][0], {"title": "Głodówka lecznicza", "snippet": "fasting"})
        self.assertIn("pl", r["agent_hint"])

    def test_disambiguation_and_redirects_detected_on_wiki(self):
        entities = {"Q1": entity({"uk": "X"}, many_wikis(10, ukwiki="X", plwiki="Y"))}
        pages = {
            ("uk", "X"): {"title": "X", "redirects": [{"title": "X1"}, {"title": "X2"}]},
            ("pl", "Y"): {"title": "Y", "pageprops": {"disambiguation": ""}},
        }
        r = resolve(qid="Q1", langs=["uk", "pl"], client=FakeClient({}, entities, pages))
        self.assertEqual(r["articles"]["uk"]["redirects_count"], 2)
        self.assertEqual(r["articles"]["pl"]["status"], "disambiguation")

    def test_falls_back_to_other_search_languages(self):
        client = FakeClient({("astronomy", "en"): ["Q333"]}, ASTRONOMY)
        r = resolve("astronomy", ["uk"], client=client)  # search_lang defaults to uk -> no hits -> en
        self.assertEqual(r["chosen"]["qid"], "Q333")

    def test_not_found(self):
        r = resolve("qwertyuiop", ["uk"], client=FakeClient({}, {}))
        self.assertEqual(r["status"], "not_found")

    def test_rejects_bad_language_codes(self):
        with self.assertRaises(ValueError):
            resolve("x", ["Ukrainian"], client=FakeClient({}, {}))


if __name__ == "__main__":
    unittest.main()
