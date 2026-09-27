"""One call for the common path: resolve -> fetch -> analyze -> chart.

Stops early (status "needs_user") only when a human decision is required:
an ambiguous topic, or a topic that was not found / has no article in any language.
Everything is cached, so follow-up questions (another language, a longer period)
are cheap to rerun with --qid.
"""

from .analyze import analyze
from .fetch import fetch
from .http import Client
from .resolve import resolve


def _resolve_summary(r):
    keep = ("status", "query", "chosen", "alternatives", "agent_hint")
    out = {k: r[k] for k in keep if k in r}
    if "articles" in r:
        out["articles"] = {l: {k: a[k] for k in ("status", "title", "search_hits", "note") if k in a}
                           for l, a in r["articles"].items()}
    if "candidates" in r:
        out["candidates"] = r["candidates"][:5]
    return out


def study(topic=None, qid=None, langs=(), search_lang=None, overrides=None, start=None, end=None,
          months=24, out_dir=None, chart_lang="en", client=None):
    client = client or Client()
    overrides = overrides or {}

    # 1. Resolve (skipped when the user already picked a meaning via --qid).
    resolved = None
    if not qid:
        resolved = resolve(topic=topic, langs=langs, search_lang=search_lang, client=client)
        if resolved["status"] != "ok":
            return {"status": "needs_user", "stage": "resolve", "resolve": _resolve_summary(resolved),
                    "agent_hint": resolved["agent_hint"]}
        qid = resolved["chosen"]["qid"]

    # 2. Fetch.
    fetched = fetch(qid=qid, langs=langs, overrides=overrides, start=start, end=end, months=months,
                    out_dir=out_dir, client=client)
    missing = dict(fetched["missing"])
    if resolved:  # add what exists instead, so the agent can offer a proxy to the user
        for lang in missing:
            hits = resolved["articles"].get(lang, {}).get("search_hits")
            if hits:
                missing[lang] = {"reason": missing[lang], "search_hits": hits}
    if fetched["status"] == "no_data":
        return {"status": "no_data", "topic": fetched["topic"], "missing": missing,
                "agent_hint": "No usable article in any requested language. Tell the user; offer a broader "
                              "or related topic, or a proxy article per language (--article LANG=TITLE)."}

    # 3. Analyze.
    analysis = analyze(fetched["dataset"])

    # 4. Charts (optional dependency).
    try:
        from .chart import chart
        charts = chart(analysis["analysis"], lang=chart_lang)["charts"]
    except ModuleNotFoundError:
        charts = None

    hints = []
    if missing:
        hints.append(f"STOP: no article in {', '.join(missing)}. Before answering or writing a report, ask the "
                     "user whether to use one of 'search_hits' as a proxy (rerun with --qid and --article "
                     "LANG=TITLE), a broader concept, or to continue without these languages. End your turn "
                     "with that question.")
    hints.append(analysis["agent_hint"])
    if charts is None:
        hints.append("Charts skipped: dependencies missing (bash scripts/setup.sh).")
    hints.append("For a shareable PDF: write content JSON and run the report command with --analysis.")

    return {
        "status": "ok" if not missing else "partial",
        "topic": fetched["topic"],
        "period": analysis["period"],
        "dataset": fetched["dataset"],
        "analysis": analysis["analysis"],
        "charts": charts,
        "results": analysis["results"],
        "comparison": analysis["comparison"],
        "missing": missing,
        "facts": analysis["facts"],
        "agent_hint": " ".join(hints),
        "requests": dict(client.stats),
    }
