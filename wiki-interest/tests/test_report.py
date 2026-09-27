"""Tests for charts and the PDF report. Need the dependencies: bash scripts/setup.sh

Run: .venv/bin/python -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import fpdf  # noqa: F401
    import matplotlib  # noqa: F401
    HAVE_DEPS = True
except ModuleNotFoundError:
    HAVE_DEPS = False

from test_analyze import dataset, make_days  # noqa: E402
from wikiinterest.analyze import analyze  # noqa: E402


def analysis_file(tmp, langs=("uk", "pl"), days=None):
    data = dataset(lambda i, d: 100 + i * 0.2, days=days)
    one = data["series"].pop("xx")
    for n, lang in enumerate(langs):
        data["series"][lang] = {**one, "title": f"Article {lang}",
                                "article": [v + 10 * n for v in one["article"]]}
    path = Path(tmp) / "ds.json"
    path.write_text(json.dumps(data))
    return Path(analyze(path)["analysis"])


def content(tmp, **overrides):
    c = {"title": "Interest is rising", "summary": "Share of attention grew in both languages.",
         "recommendations": ["Research uk next."], **overrides}
    path = Path(tmp) / "content.json"
    path.write_text(json.dumps(c, ensure_ascii=False))
    return path


@unittest.skipUnless(HAVE_DEPS, "chart/report dependencies not installed (bash scripts/setup.sh)")
class NumberCheckTest(unittest.TestCase):
    def setUp(self):
        from wikiinterest.report import unverified_numbers
        self.check = unverified_numbers
        self.analysis = {
            "period": {"start": "2024-09-01", "end": "2026-08-31", "days": 730},
            "results": {"uk": {"growth": {"normalized": -0.477, "raw": -0.596, "views_last": 16614},
                               "consistency": {"months_up": 1, "months_compared": 12},
                               "avg_daily_views": 18.4}},
        }

    def test_accepts_numbers_from_the_analysis_in_any_format(self):
        text = ("Частка впала на 47,7% (абсолютно −59.6%), 16 614 переглядів, близько 18 на день, "
                "1 місяць з 12, з 2024-09 по 2026-08, за 2 роки.")
        self.assertEqual(self.check([text], self.analysis), [])

    def test_flags_invented_numbers(self):
        self.assertEqual(self.check(["Інтерес впав на 35%, а виросте на 12.5%."], self.analysis), ["35", "12.5"])

    def test_rounding_must_be_honest(self):
        self.assertEqual(self.check(["fell 48%"], self.analysis), [])     # 47.7 -> 48 is fine
        self.assertEqual(self.check(["fell 47.2%"], self.analysis), ["47.2"])  # wrong decimal is not


@unittest.skipUnless(HAVE_DEPS, "chart/report dependencies not installed (bash scripts/setup.sh)")
class ReportTest(unittest.TestCase):
    def test_one_page_pdf_with_charts(self):
        from wikiinterest.chart import chart
        from wikiinterest.report import report
        with tempfile.TemporaryDirectory() as tmp:
            a = analysis_file(tmp)
            charts = chart(a)["charts"]
            self.assertTrue(all(Path(p).stat().st_size > 10_000 for p in charts.values()))
            r = report(a, content(tmp), lang="uk")
            self.assertEqual(r["status"], "ok")
            self.assertEqual(r["pages"], 1)
            self.assertTrue(Path(r["pdf"]).read_bytes().startswith(b"%PDF"))

    def test_many_languages_use_small_multiples(self):
        from wikiinterest.chart import chart
        with tempfile.TemporaryDirectory() as tmp:
            a = analysis_file(tmp, langs=("uk", "pl", "cs", "de", "es", "fr"))
            self.assertEqual(chart(a)["status"], "ok")

    def test_invented_number_needs_review(self):
        from wikiinterest.report import report
        with tempfile.TemporaryDirectory() as tmp:
            r = report(analysis_file(tmp), content(tmp, summary="Interest grew by 99.9%."))
            self.assertEqual(r["status"], "needs_review")
            self.assertEqual(r["unverified_numbers"], ["99.9"])

    def test_text_that_does_not_fit_is_rejected(self):
        from wikiinterest.report import report
        long_rec = "A long recommendation that keeps going and going. " * 5
        with tempfile.TemporaryDirectory() as tmp:
            a = analysis_file(tmp, langs=("uk", "pl", "cs", "de", "es", "fr", "it", "pt"),
                              days=make_days(date(2021, 9, 1), date(2026, 8, 31)))
            r = report(a, content(tmp, summary="S" * 890, recommendations=[long_rec[:290]] * 5))
            self.assertEqual(r["status"], "error")
            self.assertIn("one page", r["error"])
            self.assertFalse(Path(a.with_name("ds.report.pdf")).exists())

    def test_content_validation(self):
        from wikiinterest.report import load_content
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                load_content(content(tmp, title=""))
            with self.assertRaises(ValueError):
                load_content(content(tmp, recommendations=[]))
            with self.assertRaises(ValueError):
                load_content(content(tmp, summary="x" * 901))


if __name__ == "__main__":
    unittest.main()
