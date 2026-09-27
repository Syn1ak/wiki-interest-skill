"""Download daily pageviews for a resolved topic and save them as a dataset file.

For every language we fetch four daily series:
  article     - views of the article by humans (agent=user)
  redirects   - views of redirects pointing to the article (the API counts them separately)
  automated   - views of the article flagged as automated traffic (a trust signal)
  project     - views of the whole language edition (for normalisation)

Only daily data is downloaded; monthly numbers are sums of days. The full series goes
to a JSON file (too large for an agent's context); stdout gets a compact summary.
"""

import hashlib
import json
import os
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .http import Client, DAY
from .resolve import get_entities, site_key, validate_langs, verify_article, wikipedia_sitelinks

PAGEVIEWS_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
DATA_START = date(2015, 7, 1)  # first day available in the Pageviews API
MAX_REDIRECTS = 40             # per article; most redirects have ~0 views
SCHEMA = "wiki-interest/dataset@1"


# --- Period ----------------------------------------------------------------------------

def parse_month(value):
    try:
        return datetime.strptime(value, "%Y-%m").date()
    except ValueError:
        raise ValueError(f"Invalid month '{value}'. Use YYYY-MM, e.g. 2024-09.") from None


def month_end(first_day):
    nxt = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def resolve_period(start=None, end=None, months=24, today=None):
    """Whole months only. Default: the last `months` complete months."""
    today = today or date.today()
    last_complete = today.replace(day=1) - timedelta(days=1)
    end_day = month_end(parse_month(end)) if end else last_complete
    if start:
        start_day = parse_month(start)
    else:
        m = end_day.year * 12 + end_day.month - months
        start_day = date(m // 12, m % 12 + 1, 1)
    if end_day > last_complete:
        raise ValueError(f"End month must be complete; latest allowed is {last_complete:%Y-%m}.")
    if start_day < DATA_START:
        raise ValueError(f"Pageviews data starts in {DATA_START:%Y-%m}.")
    if start_day > end_day:
        raise ValueError("Start month is after end month.")
    return start_day, end_day


def days_between(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# --- API calls ---------------------------------------------------------------------------

def _ts(d):
    return d.strftime("%Y%m%d") + "00"


def _ttl(end, today=None):
    """Past data never changes; the most recent days may still be filled in."""
    today = today or date.today()
    return None if (today - end).days > 3 else DAY


def _to_daily(items, days):
    by_day = {i["timestamp"][:8]: i["views"] for i in items}
    return [by_day.get(d.strftime("%Y%m%d"), 0) for d in days]  # absent day = 0 views


def article_daily(client, lang, title, days, agent="user"):
    article = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = (f"{PAGEVIEWS_API}/per-article/{lang}.wikipedia.org/all-access/{agent}/"
           f"{article}/daily/{_ts(days[0])}/{_ts(days[-1])}")
    data = client.get_json(url, ttl=_ttl(days[-1]))
    return _to_daily((data or {}).get("items", []), days)  # 404 = no views recorded


def project_daily(client, lang, days):
    url = (f"{PAGEVIEWS_API}/aggregate/{lang}.wikipedia.org/all-access/user/"
           f"daily/{_ts(days[0])}/{_ts(days[-1])}")
    data = client.get_json(url, ttl=_ttl(days[-1]))
    return _to_daily((data or {}).get("items", []), days)


# --- Main entry -------------------------------------------------------------------------------

def pick_articles(client, qid, langs, overrides):
    """Article per language: user-approved override first, else the Wikidata sitelink."""
    label, links = None, {}
    if qid:
        entity = get_entities(client, [qid], list(dict.fromkeys([*langs, "en"]))).get(qid)
        if not entity or "missing" in entity:
            raise ValueError(f"Wikidata item {qid} not found.")
        labels = entity.get("labels", {})
        label = next((labels[l]["value"] for l in [*langs, "en"] if l in labels), qid)
        links = wikipedia_sitelinks(entity)

    picked, missing = {}, {}
    for lang in langs:
        proxy = lang in overrides
        title = overrides.get(lang) or links.get(site_key(lang))
        if not title:
            missing[lang] = ("No article for this concept. Pass --article "
                             f"{lang}=<title> only if the user approved a proxy article.")
            continue
        art = verify_article(client, lang, title)
        if art["status"] != "ok":
            missing[lang] = f"Article '{title}' is {art['status']}."
            continue
        art["proxy"] = proxy
        picked[lang] = art
    return label, picked, missing


def dataset_path(out_dir, qid, langs, overrides, start, end):
    name = qid or "custom"
    if overrides:
        name += "-proxy" + hashlib.sha1(json.dumps(overrides, sort_keys=True).encode()).hexdigest()[:6]
    return Path(out_dir) / f"{name}_{'-'.join(langs)}_{start:%Y-%m}_{end:%Y-%m}.json"


def fetch(qid=None, langs=(), overrides=None, start=None, end=None, months=24, out_dir=None, client=None):
    client = client or Client()
    langs = validate_langs(list(dict.fromkeys(langs)))
    overrides = {k: v for k, v in (overrides or {}).items()}
    validate_langs(list(overrides))
    if not qid and not overrides:
        raise ValueError("Pass --qid (from resolve) and/or --article lang=title.")
    unknown = set(overrides) - set(langs)
    if unknown:
        raise ValueError(f"--article given for languages not in --langs: {sorted(unknown)}")

    start_day, end_day = resolve_period(start, end, months)
    days = days_between(start_day, end_day)
    label, picked, missing = pick_articles(client, qid, langs, overrides)

    series = {}
    for lang, art in picked.items():
        redirects = art["redirects"][:MAX_REDIRECTS]
        redirect_total = [0] * len(days)
        for r in redirects:
            redirect_total = [a + b for a, b in zip(redirect_total, article_daily(client, lang, r, days))]
        series[lang] = {
            "project": f"{lang}.wikipedia.org",
            "title": art["title"],
            "url": art["url"],
            "proxy": art["proxy"],
            "redirects_included": redirects,
            "redirects_skipped": max(0, art["redirects_count"] - len(redirects))
                                 + (1 if art["redirects_truncated"] else 0),
            "article": article_daily(client, lang, art["title"], days),
            "redirects": redirect_total,
            "automated": article_daily(client, lang, art["title"], days, agent="automated"),
            "project_total": project_daily(client, lang, days),
        }

    status = "ok" if not missing else ("partial" if series else "no_data")
    dataset = {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "topic": {"qid": qid, "label": label},
        "period": {"start": start_day.isoformat(), "end": end_day.isoformat(), "days": len(days)},
        "filters": {"access": "all-access", "agent": "user"},
        "days": [d.isoformat() for d in days],
        "series": series,
        "missing": missing,
    }

    path = None
    if series:
        out_dir = out_dir or os.environ.get("WIKI_INTEREST_DATA") or "wiki-interest-data"
        path = dataset_path(out_dir, qid, langs, overrides, start_day, end_day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dataset, ensure_ascii=False))

    hints = []
    if missing:
        hints.append(f"No data for: {', '.join(missing)}. Say so in the answer; do not compare these languages.")
    if any(s["proxy"] for s in series.values()):
        hints.append("Some languages use a proxy article approved by the user; mention this as a limitation.")
    if series:
        hints.append(f"Next: python3 scripts/wi.py analyze --dataset {path}")

    return {
        "status": status,
        "dataset": str(path) if path else None,
        "topic": dataset["topic"],
        "period": dataset["period"],
        "series": {lang: _summary(s) for lang, s in series.items()},
        "missing": missing,
        "agent_hint": " ".join(hints),
        "requests": dict(client.stats),
    }


def _summary(s):
    article, redirects, automated = sum(s["article"]), sum(s["redirects"]), sum(s["automated"])
    human = article + redirects
    return {
        "title": s["title"],
        "proxy": s["proxy"],
        "views_total": human,
        "avg_daily": round(human / len(s["article"]), 1),
        "redirect_share": round(redirects / human, 3) if human else 0.0,
        "automated_share": round(automated / (article + automated), 3) if article + automated else 0.0,
        "redirects_included": len(s["redirects_included"]),
        "redirects_skipped": s["redirects_skipped"],
        "zero_days": sum(1 for a, r in zip(s["article"], s["redirects"]) if a + r == 0),
    }
