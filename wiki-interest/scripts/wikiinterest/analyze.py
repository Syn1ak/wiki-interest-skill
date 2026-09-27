"""Turn a dataset from `fetch` into metrics, trend verdicts and a confidence level.

The agent should never compute numbers itself: everything it may cite is in the
output, including ready-made `facts` sentences.

Method (all thresholds are constants below):
  * Interest = human views of the article + its redirects.
  * Spikes: days above SPIKE_RATIO x the rolling median of the surrounding days.
    "Despiked" series replace those days with the rolling median.
  * Normalised = views per million human views of the whole language edition,
    so a shrinking Wikipedia is not mistaken for shrinking interest.
  * Growth compares the last K months with the same K months one year earlier
    (K = 12 when there are 24+ months), so seasonality cancels out.
  * Consistency: in how many of those K months the value beat the same month a year before.
"""

import calendar
import json
import statistics
from collections import OrderedDict, defaultdict
from pathlib import Path

from .fetch import SCHEMA

SPIKE_WINDOW = 29         # days in the rolling-median baseline (centered)
SPIKE_RATIO = 3.0         # a day is a spike if views > ratio * baseline ...
SPIKE_MIN_EXTRA = 10      # ... and exceed the baseline by at least this many views
FLAT_BAND = 0.10          # |growth| below 10% is "flat"
CONSISTENT = 2 / 3        # share of months that must agree with the direction
LOW_VOLUME = 10           # avg human views/day: below -> low confidence
MEDIUM_VOLUME = 50        # below -> at most medium confidence
SPIKE_SHARE_WARN = 0.20   # share of views coming from spikes
AUTOMATED_WARN = 0.30     # share of automated traffic worth mentioning
AUTOMATED_BAD = 0.50      # ... worth lowering confidence
SEASONAL_PEAK = 1.5       # calendar month this many times above its year's average = seasonal peak ...
SEASONAL_REPEAT = 1.3     # ... and at least this high in every year
ANOMALY_RATIO = 2.0       # whole month this many times above its year, not repeating = anomalous
LEVELS = ["low", "medium", "high"]


# --- Series helpers ---------------------------------------------------------------------

def rolling_median(values, window=SPIKE_WINDOW):
    half = window // 2
    return [statistics.median(values[max(0, i - half):i + half + 1]) for i in range(len(values))]


def find_spikes(days, values):
    """Return (despiked values, spike episodes). Consecutive spike days form one episode."""
    base = rolling_median(values)
    despiked, episodes, current = list(values), [], None
    for i, (v, b) in enumerate(zip(values, base)):
        is_spike = v > SPIKE_RATIO * max(b, 1) and v - b >= SPIKE_MIN_EXTRA
        if is_spike:
            despiked[i] = b
            if current and current["end_i"] == i - 1:
                current["end_i"] = i
            else:
                current = {"start_i": i, "end_i": i}
                episodes.append(current)
    out = []
    for e in episodes:
        idx = range(e["start_i"], e["end_i"] + 1)
        peak = max(idx, key=lambda i: values[i])
        out.append({
            "start": days[e["start_i"]], "end": days[e["end_i"]], "peak_day": days[peak],
            "peak_views": values[peak], "typical_views": round(base[peak], 1),
            "ratio": round(values[peak] / max(base[peak], 1), 1),
            "extra_views": round(sum(values[i] - base[i] for i in idx)),
        })
    return despiked, out


def monthly(days, values):
    out = OrderedDict()
    for d, v in zip(days, values):
        out[d[:7]] = out.get(d[:7], 0) + v
    return out


def pct(new, old):
    return None if not old else round(new / old - 1, 4)


def fmt_pct(x):
    return "n/a" if x is None else f"{x:+.1%}"


# --- Per-language analysis ------------------------------------------------------------------

def analyze_series(lang, s, days):
    human = [a + r for a, r in zip(s["article"], s["redirects"])]
    despiked, spikes = find_spikes(days, human)

    m_raw = monthly(days, human)
    m_clean = monthly(days, despiked)
    m_proj = monthly(days, s["project_total"])
    months = list(m_raw)
    n = len(months)

    def norm(m):  # views per million project views
        return m_clean[m] / m_proj[m] * 1e6 if m_proj.get(m) else 0.0

    # Each month relative to the average of "its" year (12-month blocks counted from the end).
    year_ratio = {}
    for b in range(0, n, 12):
        block = months[max(0, n - b - 12):n - b]
        avg = statistics.mean(norm(m) for m in block) or 1
        for m in block:
            year_ratio[m] = norm(m) / avg
    by_cal = defaultdict(list)
    for m in months:
        by_cal[m[5:]].append(m)

    # Year-by-year view for long periods: complete 12-month blocks, oldest first.
    yearly = []
    for b in range(n // 12):
        block = months[n - (b + 1) * 12:n - b * 12]
        proj = sum(m_proj[m] for m in block)
        yearly.insert(0, {"period": f"{block[0]}..{block[-1]}", "views": sum(m_raw[m] for m in block),
                          "per_million": round(sum(m_clean[m] for m in block) / proj * 1e6, 2) if proj else None})

    # Seasonality: a calendar month that is high in EVERY year (needs two full years).
    seasonality = None
    if n >= 24:
        index = {c: statistics.mean(year_ratio[m] for m in ms) for c, ms in by_cal.items() if len(ms) > 1}
        repeats = {c for c, ms in by_cal.items() if len(ms) > 1 and min(year_ratio[m] for m in ms) >= SEASONAL_REPEAT}
        peaks = [c for c in repeats if index[c] >= SEASONAL_PEAK]
        if peaks:
            peak = max(peaks, key=index.get)
            seasonality = {"peak_month": peak, "peak_index": round(index[peak], 2)}

    # Anomalous months: the whole month far above its year, and the same month in other years is not.
    # A month-long plateau is invisible to the daily spike detector (the rolling median adapts to it).
    anomalies = {}
    for m in months:
        others = [o for o in by_cal[m[5:]] if o != m]
        if year_ratio[m] >= ANOMALY_RATIO and not any(year_ratio[o] >= SEASONAL_REPEAT for o in others):
            anomalies[m] = round(year_ratio[m], 1)

    # Windows: last K months vs the same K months a year earlier (seasonality-safe).
    if n >= 13:
        k = min(12, n - 12)
        pairs = list(zip(months[-k:], months[-k - 12:-12]))
        seasonal_safe = True
    else:
        k = n // 2
        pairs = list(zip(months[-k:], months[-2 * k:-k]))
        seasonal_safe = False
    last, prev = [a for a, _ in pairs], [b for _, b in pairs]
    # Drop a pair if either side is anomalous, so one odd month cannot fake a trend.
    kept = [(a, b) for a, b in pairs if not seasonal_safe or (a not in anomalies and b not in anomalies)]
    excluded = sorted({m for pair in pairs if pair not in kept for m in pair})

    def total(m, ms):
        return sum(m[x] for x in ms)

    def share(ms):
        proj = total(m_proj, ms)
        return total(m_clean, ms) / proj * 1e6 if proj else 0.0

    raw_last, raw_prev = total(m_raw, last), total(m_raw, prev)
    kept_last, kept_prev = [a for a, _ in kept], [b for _, b in kept]
    growth = {
        "window_months": k,
        "compared": f"{last[0]}..{last[-1]} vs {prev[0]}..{prev[-1]}" if k else None,
        "seasonality_controlled": seasonal_safe,
        "views_last": raw_last,
        "views_prev": raw_prev,
        "raw": pct(raw_last, raw_prev),  # all months, absolute views: for transparency only
        # Headline: spikes removed, normalised by project traffic, anomalous month pairs excluded.
        "normalized": pct(share(kept_last), share(kept_prev)) if kept else None,
        "excluded_months": {m: anomalies[m] for m in excluded if m in anomalies},
        "project": pct(total(m_proj, last), total(m_proj, prev)),
    }

    months_up = sum(1 for a, b in kept if norm(a) > norm(b)) if seasonal_safe else None
    compared = len(kept) if seasonal_safe else None
    consistency = {"months_up": months_up, "months_compared": compared}

    g = growth["normalized"]
    if g is None:
        trend = "unknown"
    elif abs(g) < FLAT_BAND:
        trend = "flat"
    elif not seasonal_safe:
        trend = "rising" if g > 0 else "falling"
    elif g > 0 and months_up / compared >= CONSISTENT:
        trend = "rising"
    elif g < 0 and months_up / compared <= 1 - CONSISTENT:
        trend = "falling"
    else:
        trend = "mixed"

    # Spikes that repeat in the same calendar month in different years are seasonal, not one-off.
    years_by_month = defaultdict(set)
    for sp in spikes:
        years_by_month[sp["peak_day"][5:7]].add(sp["peak_day"][:4])
    for sp in spikes:
        month = sp["peak_day"][5:7]
        sp["recurring"] = len(years_by_month[month]) > 1 or bool(seasonality and seasonality["peak_month"] == month)
    spike_extra = sum(sp["extra_views"] for sp in spikes)
    total_views = sum(human)
    spike_info = {
        "count": len(spikes),
        "extra_views_share": round(spike_extra / total_views, 3) if total_views else 0.0,
        "top": sorted(spikes, key=lambda x: -x["extra_views"])[:3],
    }

    article_total = sum(s["article"])
    automated = sum(s["automated"])
    automated_share = round(automated / (article_total + automated), 3) if article_total + automated else 0.0
    avg_daily = round(raw_last / max(1, sum(1 for d in days if d[:7] in set(last))), 1)
    leading_zero_days = next((i for i, v in enumerate(human) if v), len(human))

    confidence = assess_confidence(
        avg_daily=avg_daily, n_months=n, window=k, seasonal_safe=seasonal_safe, proxy=s["proxy"],
        spike_share=spike_info["extra_views_share"], automated_share=automated_share, trend=trend,
        growth=growth, leading_zero_days=leading_zero_days, months_up=months_up, compared=compared)

    result = {
        "title": s["title"],
        "url": s["url"],
        "proxy": s["proxy"],
        "trend": trend,
        "growth": growth,
        "consistency": consistency,
        "avg_daily_views": avg_daily,
        "share_per_million": round(share(last), 2),
        "yearly": yearly if len(yearly) > 2 else None,
        "seasonality": seasonality,
        "spikes": spike_info,
        "automated_share": automated_share,
        "confidence": confidence,
    }
    series_out = {
        "months": months,
        "views": list(m_raw.values()),
        "views_despiked": [round(v) for v in m_clean.values()],
        "per_million": [round(norm(m), 3) for m in months],
        "project_views": list(m_proj.values()),
    }
    return result, series_out


def assess_confidence(avg_daily, n_months, window, seasonal_safe, proxy, spike_share,
                      automated_share, trend, growth, leading_zero_days, months_up, compared):
    level, reasons, notes = 2, [], []

    def cap(to, why):
        nonlocal level
        level = min(level, to)
        reasons.append(why)

    def down(why):
        nonlocal level
        level = max(0, level - 1)
        reasons.append(why)

    if avg_daily < LOW_VOLUME:
        cap(0, f"very low volume ({avg_daily} views/day): a few readers can swing the numbers")
    elif avg_daily < MEDIUM_VOLUME:
        cap(1, f"low volume ({avg_daily} views/day)")
    if not seasonal_safe:
        cap(0, f"only {n_months} months of data: seasonality cannot be separated from trend")
    elif compared < 9:
        cap(1, f"only {compared} months could be compared with the same months a year earlier")
    if leading_zero_days > 30:
        cap(0, f"no views in the first {leading_zero_days} days: the article probably did not exist yet")
    if proxy:
        cap(1, "proxy article approved by the user, not the exact concept")
    if spike_share > SPIKE_SHARE_WARN:
        down(f"{spike_share:.0%} of views come from short spikes")
    if automated_share > AUTOMATED_BAD:
        down(f"{automated_share:.0%} of traffic is automated: undetected bots may remain in 'user' views")
    elif automated_share > AUTOMATED_WARN:
        notes.append(f"{automated_share:.0%} of traffic is flagged automated (excluded from the numbers)")
    if trend == "mixed":
        down(f"change is not consistent: only {months_up} of {compared} months beat the same month a year earlier")

    for m, ratio in growth["excluded_months"].items():
        notes.append(f"{m} excluded with its year-apart pair: the whole month was x{ratio} its year's average "
                     "and this does not repeat in other years (possible undetected bots, a campaign or news)")

    raw, normalized = growth["raw"], growth["normalized"]
    if raw is not None and normalized is not None and raw * normalized < 0 and min(abs(raw), abs(normalized)) > 0.05:
        notes.append("absolute views and normalised share move in opposite directions; "
                     "the answer depends on whether overall Wikipedia decline is taken into account")
    if growth["project"] is not None and growth["project"] < -0.05:
        notes.append(f"the whole language edition changed by {growth['project']:+.1%}; normalised numbers correct for this")

    if level == 2:
        reasons.append("enough volume, two full years compared, consistent year-over-year change")
    return {"level": LEVELS[level], "reasons": reasons, "notes": notes}


# --- Facts & comparison -------------------------------------------------------------------------

def facts_for(lang, r):
    g, c = r["growth"], r["consistency"]
    out = []
    if g["window_months"]:
        same = "the same months a year earlier" if g["seasonality_controlled"] else "the previous period"
        out.append(f"[{lang}] '{r['title']}': {g['views_last']} views in {g['compared'].split(' vs ')[0]} "
                   f"vs {g['views_prev']} in {same} ({fmt_pct(g['raw'])}).")
        out.append(f"[{lang}] The whole {lang}.wikipedia changed by {fmt_pct(g['project'])}; relative to it "
                   f"(spikes removed) interest changed by {fmt_pct(g['normalized'])}. Trend: {r['trend']}.")
    if g["excluded_months"]:
        out.append(f"[{lang}] Excluded as anomalous (with the month a year apart): "
                   + ", ".join(f"{m} (x{x} its year's average)" for m, x in g["excluded_months"].items()) + ".")
    if r["yearly"]:
        first, lastyear = r["yearly"][0], r["yearly"][-1]
        out.append(f"[{lang}] Per 12-month period (views per million): "
                   + ", ".join(f"{y['period']}: {y['per_million']}" for y in r["yearly"])
                   + f". Change over {len(r['yearly'])} years: {fmt_pct(pct(lastyear['per_million'], first['per_million']))}.")
    if c["months_up"] is not None:
        out.append(f"[{lang}] {c['months_up']} of {c['months_compared']} months were above the same month a year earlier.")
    if r["seasonality"]:
        out.append(f"[{lang}] Seasonal peak every {calendar.month_name[int(r['seasonality']['peak_month'])]} "
                   f"(x{r['seasonality']['peak_index']} the average month).")
    top = r["spikes"]["top"][:1]
    if top:
        sp = top[0]
        kind = "recurring (seasonal)" if sp["recurring"] else "one-off"
        out.append(f"[{lang}] Largest spike {sp['start']}..{sp['end']}: peak {sp['peak_views']} views/day, "
                   f"x{sp['ratio']} the typical {sp['typical_views']}; {kind}. Spikes = "
                   f"{r['spikes']['extra_views_share']:.0%} of all views.")
    out.append(f"[{lang}] Confidence: {r['confidence']['level']} ({'; '.join(r['confidence']['reasons'])}).")
    return out


def compare(results):
    def rank(key):
        items = [(l, key(r)) for l, r in results.items() if key(r) is not None]
        return [l for l, _ in sorted(items, key=lambda x: -x[1])]
    return {
        "by_normalized_growth": rank(lambda r: r["growth"]["normalized"]),
        "by_share_per_million": rank(lambda r: r["share_per_million"]),
        "by_avg_daily_views": rank(lambda r: r["avg_daily_views"]),
        "note": "share_per_million is comparable across languages (topic's share of each edition's attention); "
                "raw views are not, because editions differ in size.",
    }


# --- Main entry -----------------------------------------------------------------------------

def analyze(dataset_path, out_path=None):
    path = Path(dataset_path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"Cannot read dataset {path}: {e}") from None
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path} is not a wiki-interest dataset (expected schema {SCHEMA}).")

    days = data["days"]
    results, series = {}, {}
    for lang, s in data["series"].items():
        results[lang], series[lang] = analyze_series(lang, s, days)

    out_path = Path(out_path) if out_path else path.with_name(path.stem + ".analysis.json")
    full = {"schema": "wiki-interest/analysis@1", "dataset": str(path), "topic": data["topic"],
            "period": data["period"], "results": results, "monthly": series, "missing": data["missing"]}
    out_path.write_text(json.dumps(full, ensure_ascii=False))

    facts = [f for lang, r in results.items() for f in facts_for(lang, r)]
    hints = ["Base every claim on 'facts' or the numbers below; do not compute new numbers.",
             "State the confidence level and its reasons for each language."]
    if data["missing"]:
        hints.append(f"No data for: {', '.join(data['missing'])}; say so explicitly.")
    if any(r["proxy"] for r in results.values()):
        hints.append("Mention that proxy articles were used where marked.")

    return {
        "status": "ok",
        "analysis": str(out_path),
        "topic": data["topic"],
        "period": data["period"],
        "results": results,
        "comparison": compare(results) if len(results) > 1 else None,
        "missing": data["missing"],
        "facts": facts,
        "agent_hint": " ".join(hints),
    }
