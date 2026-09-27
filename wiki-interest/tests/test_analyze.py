"""Offline tests for analysis on synthetic datasets with known answers.

Run: python3 -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from wikiinterest.analyze import analyze, find_spikes  # noqa: E402
from wikiinterest.fetch import SCHEMA  # noqa: E402


def make_days(start=date(2024, 9, 1), end=date(2026, 8, 31)):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def dataset(article_fn, project_fn=lambda i, d: 1_000_000, days=None, proxy=False, automated=0):
    days = days or make_days()
    series = {
        "project": "xx.wikipedia.org", "title": "Topic", "url": "https://xx.wikipedia.org/wiki/Topic",
        "proxy": proxy, "redirects_included": [], "redirects_skipped": 0,
        "article": [round(article_fn(i, d)) for i, d in enumerate(days)],
        "redirects": [0] * len(days),
        "automated": [automated] * len(days),
        "project_total": [round(project_fn(i, d)) for i, d in enumerate(days)],
    }
    return {
        "schema": SCHEMA, "topic": {"qid": "Q1", "label": "topic"},
        "period": {"start": days[0].isoformat(), "end": days[-1].isoformat(), "days": len(days)},
        "filters": {"access": "all-access", "agent": "user"},
        "days": [d.isoformat() for d in days], "series": {"xx": series}, "missing": {},
    }


def run(data):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ds.json"
        path.write_text(json.dumps(data))
        out = analyze(path)
        full = json.loads(Path(out["analysis"]).read_text())
    return out["results"]["xx"], out, full


class SpikeTest(unittest.TestCase):
    def test_single_day_spike_is_found_and_removed(self):
        days = [f"2025-01-{i:02d}" for i in range(1, 32)]
        values = [100] * 31
        values[15] = 900
        despiked, spikes = find_spikes(days, values)
        self.assertEqual(len(spikes), 1)
        self.assertEqual(spikes[0]["peak_day"], "2025-01-16")
        self.assertEqual(spikes[0]["extra_views"], 800)
        self.assertEqual(despiked[15], 100)

    def test_small_absolute_jump_is_not_a_spike(self):
        values = [2] * 31
        values[10] = 9  # 4.5x but only +7 views
        self.assertEqual(find_spikes([str(i) for i in range(31)], values)[1], [])


class AnalyzeTest(unittest.TestCase):
    def test_steady_growth_is_rising_with_high_confidence(self):
        r, out, full = run(dataset(lambda i, d: 100 + i * 0.2))
        self.assertEqual(r["trend"], "rising")
        self.assertEqual(r["consistency"], {"months_up": 12, "months_compared": 12})
        self.assertEqual(r["confidence"]["level"], "high")
        self.assertGreater(r["growth"]["normalized"], 0.3)
        self.assertEqual(len(full["monthly"]["xx"]["months"]), 24)
        self.assertTrue(any("12 of 12" in f for f in out["facts"]))

    def test_shrinking_wikipedia_is_not_shrinking_interest(self):
        # Article and whole project both lose ~30%: absolute views fall, the share does not.
        decay = lambda i, d: 1 - 0.3 * i / 730
        r, _, _ = run(dataset(lambda i, d: 200 * decay(i, d), lambda i, d: 1_000_000 * decay(i, d)))
        self.assertLess(r["growth"]["raw"], -0.1)
        self.assertEqual(r["trend"], "flat")
        self.assertTrue(any("whole language edition" in n for n in r["confidence"]["notes"]))

    def test_one_day_spike_does_not_fake_growth(self):
        r, _, _ = run(dataset(lambda i, d: 5000 if d == date(2026, 3, 10) else 100))
        self.assertGreater(r["growth"]["raw"], 0.1)       # raw numbers are fooled
        self.assertEqual(r["trend"], "flat")              # headline is not
        self.assertEqual(r["spikes"]["count"], 1)
        self.assertFalse(r["spikes"]["top"][0]["recurring"])

    def test_month_long_plateau_is_excluded_as_anomalous(self):
        r, _, _ = run(dataset(lambda i, d: 400 if (d.year, d.month) == (2025, 11) else 100))
        self.assertEqual(r["spikes"]["count"], 0)         # invisible to the daily detector
        self.assertIn("2025-11", r["growth"]["excluded_months"])
        self.assertEqual(r["consistency"]["months_compared"], 11)
        self.assertEqual(r["trend"], "flat")

    def test_repeating_month_is_seasonal_not_anomalous(self):
        r, _, _ = run(dataset(lambda i, d: 400 if d.month == 9 else 100))
        self.assertEqual(r["seasonality"]["peak_month"], "09")
        self.assertEqual(r["growth"]["excluded_months"], {})
        self.assertEqual(r["trend"], "flat")

    def test_low_volume_means_low_confidence(self):
        r, _, _ = run(dataset(lambda i, d: 5 + (i > 365) * 3))
        self.assertEqual(r["confidence"]["level"], "low")
        self.assertTrue(any("very low volume" in x for x in r["confidence"]["reasons"]))

    def test_short_period_cannot_control_seasonality(self):
        r, _, _ = run(dataset(lambda i, d: 100 + i, days=make_days(date(2026, 1, 1), date(2026, 8, 31))))
        self.assertFalse(r["growth"]["seasonality_controlled"])
        self.assertEqual(r["confidence"]["level"], "low")
        self.assertIsNone(r["consistency"]["months_up"])

    def test_inconsistent_change_is_mixed(self):
        # Last year higher only in 4 strong months (not anomalous enough to exclude).
        up = {(2025, 9), (2025, 10), (2025, 11), (2025, 12)}
        r, _, _ = run(dataset(lambda i, d: 180 if (d.year, d.month) in up else 100))
        self.assertEqual(r["trend"], "mixed")
        self.assertTrue(any("not consistent" in x for x in r["confidence"]["reasons"]))

    def test_proxy_and_bots_lower_confidence(self):
        r, _, _ = run(dataset(lambda i, d: 100 + i * 0.2, proxy=True, automated=300))  # ~63% automated
        self.assertEqual(r["confidence"]["level"], "low")
        reasons = " ".join(r["confidence"]["reasons"])
        self.assertIn("proxy", reasons)
        self.assertIn("automated", reasons)

    def test_long_period_has_yearly_breakdown(self):
        r, out, _ = run(dataset(lambda i, d: 100 + i * 0.05, days=make_days(date(2021, 9, 1), date(2026, 8, 31))))
        self.assertEqual(len(r["yearly"]), 5)
        self.assertTrue(any("Change over 5 years" in f for f in out["facts"]))

    def test_rejects_non_dataset_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            path.write_text("{}")
            with self.assertRaises(ValueError):
                analyze(path)


if __name__ == "__main__":
    unittest.main()
