"""Resolve a free-text topic into concrete Wikipedia articles in several languages.

Pipeline:
  1. Search Wikidata for the topic -> candidate concepts (QIDs).
  2. Drop candidates that are not real encyclopedic topics (disambiguation pages,
     scholarly papers, categories, items without any Wikipedia article).
  3. Rank by coverage of the requested languages, then by how many Wikipedias have
     an article (a rough "this is the main meaning" signal). Flag ambiguity.
  4. For the chosen concept, take its sitelinks (the exact article title in each
     language) and verify every article on its own wiki: exists, not a
     disambiguation page, and collect redirects (their views are counted separately).
  5. For languages without an article, run a full-text search so the agent can tell
     the user what exists instead - clearly marked as NOT equivalent.
"""

import re

from .http import Client

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Items that are instances of these classes are never a "topic of interest".
EXCLUDED_CLASSES = {
    "Q4167410": "disambiguation page",
    "Q4167836": "Wikimedia category",
    "Q11266439": "Wikimedia template",
    "Q13442814": "scholarly article",
    "Q30612": "clinical trial",
    "Q19389637": "biographical article",
    "Q17633526": "Wikinews article",
}

# Sitelink keys ending in "wiki" that are not language Wikipedias.
NON_WIKIPEDIA_SITES = {
    "commonswiki", "specieswiki", "metawiki", "mediawikiwiki", "wikidatawiki",
    "sourceswiki", "wikifunctionswiki", "foundationwiki", "incubatorwiki", "outreachwiki",
    "wikimaniawiki", "testwiki", "test2wiki", "testwikidatawiki",
}

# A second candidate this popular (relative to the top one) means the query is ambiguous.
AMBIGUITY_RATIO = 0.3

LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]+)*$")
TAG_RE = re.compile(r"<[^>]+>")


def site_key(lang):
    return lang.replace("-", "_") + "wiki"


def wiki_api(lang):
    return f"https://{lang}.wikipedia.org/w/api.php"


def article_url(lang, title):
    return f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"


def validate_langs(langs):
    bad = [l for l in langs if not LANG_RE.match(l)]
    if bad:
        raise ValueError(f"Invalid language code(s): {bad}. Use Wikipedia codes like uk, pl, cs, en, pt, zh.")
    return langs


# --- Wikidata ------------------------------------------------------------------

def search_entities(client, query, lang, limit=8):
    data = client.get_json(WIKIDATA_API, {
        "action": "wbsearchentities", "search": query, "language": lang, "uselang": lang,
        "type": "item", "limit": limit, "format": "json",
    })
    return [c["id"] for c in (data or {}).get("search", [])]


def get_entities(client, qids, langs):
    """Fetch labels, descriptions, sitelinks and P31 (instance of) for up to 50 items."""
    if not qids:
        return {}
    data = client.get_json(WIKIDATA_API, {
        "action": "wbgetentities", "ids": "|".join(qids),
        "props": "labels|descriptions|sitelinks|claims",
        "languages": "|".join(langs), "format": "json",
    })
    return (data or {}).get("entities", {})


def instance_of(entity):
    claims = entity.get("claims", {}).get("P31", [])
    out = []
    for c in claims:
        value = c.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and "id" in value:
            out.append(value["id"])
    return out


def wikipedia_sitelinks(entity):
    return {k: v["title"] for k, v in entity.get("sitelinks", {}).items()
            if k.endswith("wiki") and k not in NON_WIKIPEDIA_SITES}


def pick_text(entity, field, langs):
    values = entity.get(field, {})
    for lang in langs:
        if lang in values:
            return values[lang]["value"]
    return next((v["value"] for v in values.values()), "")


def summarize_candidate(qid, entity, langs, display_langs):
    links = wikipedia_sitelinks(entity)
    excluded = [EXCLUDED_CLASSES[c] for c in instance_of(entity) if c in EXCLUDED_CLASSES]
    return {
        "qid": qid,
        "label": pick_text(entity, "labels", display_langs),
        "description": pick_text(entity, "descriptions", display_langs),
        "wikipedias_total": len(links),
        "covered_langs": [l for l in langs if site_key(l) in links],
        "excluded_reason": excluded[0] if excluded else ("no Wikipedia article" if not links else None),
    }


def rank_candidates(candidates):
    """Usable candidates first: most requested languages covered, then most Wikipedias."""
    usable = [c for c in candidates if not c["excluded_reason"]]
    usable.sort(key=lambda c: (-len(c["covered_langs"]), -c["wikipedias_total"]))
    return usable


def is_ambiguous(ranked):
    if len(ranked) < 2:
        return False
    top, second = ranked[0], ranked[1]
    return bool(second["covered_langs"]) and second["wikipedias_total"] >= AMBIGUITY_RATIO * top["wikipedias_total"]


# --- Per-wiki verification ---------------------------------------------------------

def verify_article(client, lang, title):
    """Check the article on its own wiki; resolve redirects; list redirects pointing to it."""
    data = client.get_json(wiki_api(lang), {
        "action": "query", "titles": title, "redirects": 1,
        "prop": "pageprops|redirects", "ppprop": "disambiguation",
        "rdnamespace": 0, "rdlimit": "max", "format": "json", "formatversion": 2,
    })
    pages = (data or {}).get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or pages[0].get("invalid"):
        return {"status": "missing_on_wiki", "title": title}

    page = pages[0]
    redirects = [r["title"] for r in page.get("redirects", [])]
    result = {
        "status": "ok",
        "title": page["title"],
        "url": article_url(lang, page["title"]),
        "redirects_count": len(redirects),
        "redirects_truncated": "continue" in data,
        "redirects": redirects,
    }
    if page["title"] != title:
        result["resolved_from"] = title
    if "disambiguation" in page.get("pageprops", {}):
        result["status"] = "disambiguation"
    return result


def search_wiki(client, lang, query, limit=3):
    data = client.get_json(wiki_api(lang), {
        "action": "query", "list": "search", "srsearch": query, "srlimit": limit,
        "srnamespace": 0, "srprop": "snippet", "format": "json", "formatversion": 2,
    })
    hits = (data or {}).get("query", {}).get("search", [])
    return [{"title": h["title"], "snippet": TAG_RE.sub("", h.get("snippet", ""))[:160]} for h in hits]


# --- Main entry ----------------------------------------------------------------------

def resolve(topic=None, langs=(), search_lang=None, qid=None, client=None, max_redirects_shown=5):
    """Return a JSON-serializable dict describing which articles represent the topic."""
    client = client or Client()
    langs = validate_langs(list(dict.fromkeys(langs)))
    if not topic and not qid:
        raise ValueError("Pass a topic or a qid.")

    # 1-3. Find and rank candidate concepts.
    if qid:
        qids = [qid]
    else:
        search_langs = list(dict.fromkeys([search_lang or langs[0], *langs, "en"]))
        qids = []
        for sl in search_langs:  # fall back to other languages only if nothing matched
            qids = search_entities(client, topic, sl)
            if qids:
                break

    # Labels/descriptions are shown in the user's language first, then English.
    display_langs = list(dict.fromkeys([search_lang or langs[0], "en", *langs]))
    entities = get_entities(client, qids, display_langs)
    candidates = [summarize_candidate(q, entities[q], langs, display_langs)
                  for q in qids if q in entities and "missing" not in entities[q]]
    ranked = rank_candidates(candidates)

    base = {"query": topic, "langs": langs}
    if not ranked:
        return {**base, "status": "not_found", "candidates": candidates,
                "agent_hint": "No usable Wikipedia topic found. Ask the user to rephrase or give the topic in English."}

    chosen = ranked[0]
    entity = entities[chosen["qid"]]
    links = wikipedia_sitelinks(entity)

    # 4-5. Verify each requested language, search where there is no article.
    articles = {}
    for lang in langs:
        title = links.get(site_key(lang))
        if title:
            art = verify_article(client, lang, title)
            art["redirects"] = art.get("redirects", [])[:max_redirects_shown]
        else:
            labels = entity.get("labels", {})
            label = labels.get(lang, {}).get("value")
            q = label or labels.get("en", {}).get("value") or topic or chosen["label"]
            art = {"status": "no_article", "wikidata_label": label, "search_query": q,
                   "search_hits": search_wiki(client, lang, q),
                   "note": "No article for this concept. Search hits only mention the words; they are NOT equivalent."}
        articles[lang] = art

    ok_langs = [l for l, a in articles.items() if a["status"] == "ok"]
    if qid:
        status = "ok"
    elif is_ambiguous(ranked):
        status = "ambiguous"
    else:
        status = "ok"
    if not ok_langs:
        status = "no_articles_in_langs"

    hints = []
    if status == "ambiguous":
        hints.append("Several distinct meanings. Show the user 'alternatives', ask which one they mean, "
                     "then rerun with --qid <QID>. Do not guess.")
    missing = [l for l in langs if articles[l]["status"] != "ok"]
    if missing:
        hints.append(f"No usable article in: {', '.join(missing)}. Tell the user; data for these languages "
                     "cannot be compared. Do not substitute search hits without the user's approval.")
    if any(a.get("status") == "ok" and a["redirects_count"] for a in articles.values()):
        hints.append("Redirects exist; their views are counted separately and will be added when fetching views.")

    return {
        **base,
        "status": status,
        "chosen": {k: chosen[k] for k in ("qid", "label", "description", "wikipedias_total")},
        "articles": articles,
        "alternatives": [{k: c[k] for k in ("qid", "label", "description", "wikipedias_total", "covered_langs")}
                         for c in ranked[1:5]],
        "excluded": [{"qid": c["qid"], "label": c["label"], "reason": c["excluded_reason"]}
                     for c in candidates if c["excluded_reason"]],
        "agent_hint": " ".join(hints) or "Topic resolved unambiguously.",
        "requests": dict(client.stats),
    }
