# wiki-interest

An [Agent Skill](https://agentskills.io/specification) that helps B2C founders decide **which topics to develop next and which languages to launch in**, using [Wikipedia pageview statistics](https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html) as a signal of audience interest.

Example questions the skill is being built to answer:

- *Compare the growth of interest in intermittent fasting in Polish and Czech Wikipedia over the last two years.*
- *We are thinking of adding an astronomy course. Is interest growing in Ukrainian Wikipedia, and how much can we trust that growth?*
- *Compare interest in learning English across our chosen language editions and prepare a short report: which audiences should we research next and why?*

> **Status: work in progress.** This README describes the work step by step as it is done.

## Core design principle: code does the data work, the agent does the judgment

The skill must work well on a fast, cheap model (e.g. Claude Haiku 4.5). Small models are good at understanding intent and writing prose, but unreliable at arithmetic and multi-step data handling. So the work is split like this:

| Code (deterministic, same result every time) | Agent (language and judgment) |
|---|---|
| Calling the APIs, rate limiting, retries, caching | Understanding what the user wants: topics, languages, period |
| Mapping a topic to the exact article in each language | Asking the user when a topic is ambiguous |
| Cleaning data, computing metrics and trends, detecting anomalies | Choosing which command to run and with which arguments |
| Charts and PDF rendering | Explaining results *based on the JSON the code returns* |

Every command prints **one JSON document** with a `status` field and an `agent_hint` telling the agent what to do next.

## Repository layout

```
.
├── README.md
├── explore/                      # Step 1: throwaway exploration, NOT part of the skill
│   ├── explore_api.py
│   └── FINDINGS.md
└── wiki-interest/                # the skill itself (everything it needs lives here)
    ├── scripts/
    │   ├── wi.py                 # CLI entry point: python3 scripts/wi.py <command>
    │   └── wikiinterest/
    │       ├── http.py           # shared HTTP client: User-Agent, throttling, retries, disk cache
    │       └── resolve.py        # Step 2: topic -> Wikipedia articles in several languages
    └── tests/
        └── test_resolve.py       # offline unit tests (fake API responses)
```

## Requirements

- Python 3.10+, standard library only (no third-party packages yet).
- Internet access to `wikidata.org`, `*.wikipedia.org` and `wikimedia.org`.

Optional environment variables:

| Variable | Purpose |
|---|---|
| `WIKI_INTEREST_CONTACT` | Contact info (email or URL) added to the User-Agent, as required by the [Wikimedia User-Agent policy](https://meta.wikimedia.org/wiki/User-Agent_policy) |
| `WIKI_INTEREST_CACHE` | Cache directory (default: `wiki-interest/.cache/`) |

---

## Step 1: Exploring the API by hand

**What we did.** Wrote a standalone script, [`explore/explore_api.py`](explore/explore_api.py), that walks through the whole data path once, for one topic:

1. search the topic in Wikidata;
2. get the article title in each language;
3. download monthly views;
4. normalise them by the total views of each language edition;
5. compare human and bot traffic;
6. look for daily spikes.

```bash
python3 explore/explore_api.py
python3 explore/explore_api.py --query "intermittent fasting" --search-lang en --langs pl,cs,en
```

**Why.** Before designing the skill, we needed to see what the real data looks like and where it can mislead. The question "how much can we trust this growth?" can only be answered once you know how the data goes wrong.

**What we learned** (full notes in [`explore/FINDINGS.md`](explore/FINDINGS.md)):

1. **Rate limits are real.** After ~10 fast requests the API returned HTTP 429, once with a 43 s wait. → We need throttling, retries and caching.
2. **The first search result is not always right.** Candidates for "астрономія" include the Hogwarts class. → Topic matching needs filtering and ambiguity checks.
3. **An article may simply not exist.** Intermittent fasting, the first example from the task, has no Polish article. → The skill must say "no data" instead of inventing an answer.
4. **Seasonality.** Ukrainian *Астрономія* gets 3–5× more views every September (school year). → Compare year over year, not month to month.
5. **Wikipedia itself is shrinking.** All of Ukrainian Wikipedia lost ~25% of views in a year. *Астрономія* fell ~60% in absolute terms but ~45% after normalisation. → Without normalisation, "interest is falling" is overstated.
6. **Bots.** Almost half of the traffic to Ukrainian *Астрономія* was `automated` or `spider`. → Always use `agent=user`.
7. **Low volume means noise.** At ~15 views/day, one school class creates a visible *spike* (a short, sharp jump followed by a return to normal). → Trends on low-volume articles must be marked as low-confidence.

**Next step.** The first thing every question needs is to turn the user's topic into exact article titles. Finding #2 and finding #3 showed this is not trivial, so it became Step 2.

---

## Step 2: `resolve`, topic → articles in each language

**What we did.** The first real command of the skill, plus a shared HTTP client that every future command will use.

```bash
python3 wiki-interest/scripts/wi.py resolve --topic "астрономія" --langs uk,pl,cs
python3 wiki-interest/scripts/wi.py resolve --topic "intermittent fasting" --search-lang en --langs pl,cs
python3 wiki-interest/scripts/wi.py resolve --qid Q333 --langs uk,pl,cs   # after the user picked a meaning
```

**Why.** The Pageviews API needs an exact article title per language (`uk.wikipedia.org` + `Астрономія`). If the agent translated the topic itself, it would be guessing, and a wrong title silently produces wrong numbers. Instead we use **Wikidata**: every concept has an ID (astronomy = `Q333`) with *sitelinks*, which are the official titles of its article in every Wikipedia. This makes the question "is this really the topic the user meant?" checkable, and the answer visible to the user.

**How it works:**

1. **Search Wikidata** for the topic in the user's language. If there are no hits, fall back to the other requested languages and English.
2. **Drop non-topics**: disambiguation pages, scholarly papers, clinical trials, categories, templates, and items with no Wikipedia article.
3. **Rank candidates** by how many of the requested languages have an article, then by the total number of Wikipedias that have one (a rough "main meaning" signal).
4. **Flag ambiguity.** If the runner-up is at least 30% as widespread as the top candidate, the status is `ambiguous`. The agent must ask the user and rerun with `--qid`, not guess.
5. **Verify each article on its own wiki**: it exists, it is not a disambiguation page, and the redirects pointing to it are collected. The API counts views of a redirect (e.g. Czech *Hvězdářství* → *Astronomie*) separately, so they must be added to the article later.
6. **Handle missing languages.** If a language has no article, run a full-text search and return the hits, explicitly marked as *not equivalent*. The agent may offer them to the user as a proxy, but must never substitute them silently.

Statuses: `ok`, `ambiguous`, `not_found`, `no_articles_in_langs`, `error`.

**Shared HTTP client** ([`http.py`](wiki-interest/scripts/wikiinterest/http.py)). It solves finding #1 once for every future command:

- a **polite User-Agent** with contact info, which Wikimedia requires;
- **throttling** between requests and **retries** on HTTP 429/5xx, honouring `Retry-After`;
- a **disk cache** keyed by URL, with a TTL (7 days by default, or forever for data that cannot change, such as past pageviews). Follow-up questions ("now add Slovak") then only fetch what is new.

**Results on real data:**

| Query | Result |
|---|---|
| `астрономія` · uk,pl,cs | `ok`: Q333, all three articles found, a Wikidata-internal item excluded |
| `Меркурій` · uk,pl,cs | `ambiguous`: planet (250 wikis) vs mercury the element (176) vs the god (84) |
| `intermittent fasting` · pl,cs | cs `ok`, **pl has no article**. Search suggests *Głodówka lecznicza* (therapeutic fasting) as a possible proxy |
| `English as a second or foreign language` · uk,pl,de,es | de, es `ok`, **uk and pl have no article**. The third example from the task needs a broader proxy topic, agreed with the user |
| repeated `астрономія` | 0 network requests, served from cache |

**Tests.** Offline and deterministic, using a fake client with fixture responses. They cover choosing the main meaning, excluding junk, ambiguity detection, explicit `--qid`, missing languages, disambiguation and redirect detection, search-language fallback, and invalid language codes.

```bash
python3 -m unittest discover -s wiki-interest/tests -v
```

**Next step.** `fetch`: download views for the resolved articles, adding the views of their redirects, with `agent=user` (finding #6) and the total views of each language edition for normalisation (finding #5). Everything is cached through the shared client.
