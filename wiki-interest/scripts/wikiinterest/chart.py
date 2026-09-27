"""Render charts from an analysis file (`*.analysis.json`) with matplotlib.

Two PNGs:
  trend  - monthly share of attention (views per million, spikes removed), one line per
           language. One y-axis for all languages: share per million is comparable across
           editions of different sizes, raw views are not.
  growth - headline year-over-year growth per language, with the confidence level.

Colours follow a colour-blind-validated categorical palette in a fixed order. Three slots are
below 3:1 contrast on white, so every line is also labelled directly and the report has a table.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display needed
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from datetime import date  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
POSITIVE, NEGATIVE, NEUTRAL = "#2a78d6", "#e34948", "#c3c2b7"
INK, INK_2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#ffffff"
MM = 1 / 25.4  # inches per millimetre
MAX_LINES_ONE_PANEL = 4

LABELS = {
    "en": {"trend_title": "Share of attention by month",
           "trend_sub": "Views per million pageviews of each language edition, spikes removed",
           "excluded": "excluded month (anomaly)",
           "growth_title": "Year-over-year change in share of attention",
           "confidence": "confidence"},
    "uk": {"trend_title": "Частка уваги по місяцях",
           "trend_sub": "Переглядів на мільйон переглядів мовного розділу, без сплесків",
           "excluded": "виключений місяць (аномалія)",
           "growth_title": "Зміна частки уваги рік до року",
           "confidence": "довіра"},
}
CONFIDENCE = {"uk": {"high": "висока", "medium": "середня", "low": "низька"}}


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(0.75)
    ax.tick_params(colors=MUTED, labelsize=7, length=0, pad=4)
    ax.grid(axis="y", color=GRID, linewidth=0.75)
    ax.set_axisbelow(True)


def _month_dates(months):
    return [date(int(m[:4]), int(m[5:7]), 15) for m in months]


def _spread_labels(ys, min_gap):
    """Nudge end-label positions apart so they never overlap."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for a, b in zip(order, order[1:]):
        if out[b] - out[a] < min_gap:
            out[b] = out[a] + min_gap
    return out


def _ymax(items):
    return max(max(s["per_million"]) for _, s, _, _ in items) or 1


def _panel(ax, items, labels, show_end_labels, tick_months=(1, 7), ymax=None):
    ymax = ymax or _ymax(items)
    ends = []
    for lang, s, color, excluded in items:
        x = _month_dates(s["months"])
        y = s["per_million"]
        ax.plot(x, y, color=color, linewidth=1.5, solid_joinstyle="round", solid_capstyle="round", label=lang)
        ax.plot(x[-1], y[-1], "o", color=color, markersize=5, markeredgecolor=SURFACE, markeredgewidth=1)
        ex = [(xi, yi) for xi, yi, m in zip(x, y, s["months"]) if m in excluded]
        if ex:
            ax.plot(*zip(*ex), "o", markerfacecolor=SURFACE, markeredgecolor=color, markersize=6, markeredgewidth=1.5)
        ends.append((lang, x[-1], y[-1], color))
    ax.set_ylim(0, ymax * 1.12)
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=tick_months))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    _style(ax)
    if show_end_labels:
        ys = _spread_labels([e[2] for e in ends], ymax * 0.07)
        for (lang, x, y, _), ly in zip(ends, ys):
            ax.annotate(f"{lang}  {y:.1f}", (x, ly), xytext=(8, 0), textcoords="offset points",
                        va="center", fontsize=7.5, color=INK, annotation_clip=False)


def trend_chart(analysis, out_path, lang="en", width_mm=180, height_mm=72):
    L = LABELS.get(lang, LABELS["en"])
    langs = list(analysis["monthly"])
    items = [(l, analysis["monthly"][l], SERIES[i % len(SERIES)],
              set(analysis["results"][l]["growth"]["excluded_months"])) for i, l in enumerate(langs)]
    any_excluded = any(ex for *_, ex in items)

    if len(items) <= MAX_LINES_ONE_PANEL:
        fig, ax = plt.subplots(figsize=(width_mm * MM, height_mm * MM), dpi=200)
        _panel(ax, items, L, show_end_labels=True)
        axes = [ax]
    else:  # small multiples, shared y, so many lines never tangle
        cols = 3
        rows = -(-len(items) // cols)
        fig, grid = plt.subplots(rows, cols, figsize=(width_mm * MM, (height_mm * 0.6 * rows) * MM),
                                 dpi=200, sharey=True, squeeze=False)
        axes = [a for row in grid for a in row]
        for ax, item in zip(axes, items):
            _panel(ax, [item], L, show_end_labels=False, tick_months=(1,), ymax=_ymax(items))
            ax.set_title(item[0], fontsize=8, color=INK, loc="left")
        for ax in axes[len(items):]:
            ax.axis("off")

    fig.patch.set_facecolor(SURFACE)
    header_mm = 13  # title + subtitle band, fixed in millimetres so it does not depend on figure height
    fig_h_mm = fig.get_figheight() / MM
    fig.text(0.01, 1 - 4 / fig_h_mm, L["trend_title"], ha="left", va="center", fontsize=10, color=INK,
             fontweight="bold")
    fig.text(0.01, 1 - 9 / fig_h_mm, L["trend_sub"], ha="left", va="center", fontsize=7.5, color=INK_2)
    handles, names = axes[0].get_legend_handles_labels()
    if any_excluded:
        handles.append(plt.Line2D([], [], marker="o", linestyle="", markerfacecolor=SURFACE,
                                  markeredgecolor=MUTED, markersize=6, markeredgewidth=1.5))
        names.append(L["excluded"])
    if len(items) <= MAX_LINES_ONE_PANEL and (len(items) > 1 or any_excluded):
        fig.legend(handles, names, loc="center right", ncol=len(names), frameon=False, fontsize=7,
                   labelcolor=INK_2, bbox_to_anchor=(0.99, 1 - 4 / fig_h_mm))
    fig.tight_layout(rect=(0, 0, 0.93, 1 - header_mm / fig_h_mm))
    fig.savefig(out_path, facecolor=SURFACE)
    plt.close(fig)
    return out_path


def growth_chart(analysis, out_path, lang="en", width_mm=180):
    L = LABELS.get(lang, LABELS["en"])
    rows = [(l, r["growth"]["normalized"], r["confidence"]["level"], r["trend"])
            for l, r in analysis["results"].items() if r["growth"]["normalized"] is not None]
    if not rows:  # too short a period to compare anything
        return None
    rows.sort(key=lambda x: x[1])
    height_mm = 22 + 8 * len(rows)
    fig, ax = plt.subplots(figsize=(width_mm * MM, height_mm * MM), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ys = range(len(rows))
    values = [g * 100 for _, g, _, _ in rows]
    # Colour follows the verdict, so a "flat" -5% is not painted as a decline.
    colors = [{"rising": POSITIVE, "falling": NEGATIVE}.get(t, NEUTRAL) for *_, t in rows]
    ax.barh(list(ys), values, height=0.55, color=colors)
    span = max(abs(v) for v in values) or 1
    for y, v, (_, _, conf, _) in zip(ys, values, rows):
        conf_txt = CONFIDENCE.get(lang, {}).get(conf, conf)
        ax.text(v + (span * 0.03 if v >= 0 else -span * 0.03), y, f"{v:+.1f}%  ({L['confidence']}: {conf_txt})",
                va="center", ha="left" if v >= 0 else "right", fontsize=7.5, color=INK)
    ax.axvline(0, color=AXIS, linewidth=0.75)
    ax.set_yticks(list(ys), [row[0] for row in rows], fontsize=8, color=INK)
    # Room for labels only on the side where bars (and their labels) are.
    left = min(values) - span * 0.9 if min(values) < 0 else -span * 0.05
    right = max(values) + span * 0.9 if max(values) > 0 else span * 0.05
    ax.set_xlim(left, right)
    ax.set_xticks([])
    _style(ax)
    ax.grid(False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.set_title(L["growth_title"], loc="left", fontsize=10, color=INK, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_path, facecolor=SURFACE)
    plt.close(fig)
    return out_path


def load_analysis(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"Cannot read analysis {path}: {e}") from None
    if data.get("schema") != "wiki-interest/analysis@1":
        raise ValueError(f"{path} is not an analysis file; run analyze first and pass its 'analysis' path.")
    if not data["results"]:
        raise ValueError("Analysis has no languages with data; nothing to chart.")
    return data


def chart(analysis_path, lang="en"):
    path = Path(analysis_path)
    data = load_analysis(path)
    stem = path.name.removesuffix(".analysis.json")
    trend = trend_chart(data, path.with_name(f"{stem}.trend.png"), lang)
    growth = growth_chart(data, path.with_name(f"{stem}.growth.png"), lang)
    return {
        "status": "ok",
        "charts": {"trend": str(trend), "growth": str(growth) if growth else None},
        "agent_hint": "Show or attach these PNGs. For a shareable one-page PDF run the report command.",
    }
