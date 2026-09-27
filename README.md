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
    ├── SKILL.md                  # Step 6: instructions for the agent (Agent Skills format)
    ├── references/
    │   ├── methodology.md        # how every number and confidence level is computed
    │   └── examples.md           # procedures for the three typical requests
    ├── scripts/
    │   ├── wi.py                 # CLI entry point: python3 scripts/wi.py <command>
    │   └── wikiinterest/
    │       ├── http.py           # shared HTTP client: User-Agent, throttling, retries, disk cache
    │       ├── resolve.py        # Step 2: topic -> Wikipedia articles in several languages
    │       ├── fetch.py          # Step 3: daily pageviews -> dataset file
    │       ├── analyze.py        # Step 4: dataset -> growth, trend, spikes, confidence
    │       ├── chart.py          # Step 5: analysis -> PNG charts
    │       ├── report.py         # Step 5: analysis + agent text -> one-page PDF
    │       └── study.py          # Step 6: resolve -> fetch -> analyze -> chart in one call
    ├── scripts/setup.sh          # creates .venv with pinned dependencies
    ├── requirements.txt          # pinned dependencies for chart/report
    └── tests/                    # offline unit tests (fake API responses, synthetic data)
        ├── test_resolve.py
        ├── test_fetch.py
        ├── test_analyze.py
        ├── test_report.py
        └── test_study.py
```

## Requirements

- Python 3.11+.
- `resolve`, `fetch` and `analyze` use only the standard library.
- `chart` and `report` need `matplotlib` and `fpdf2`, pinned in [`requirements.txt`](wiki-interest/requirements.txt). Install them into the skill's own virtual environment:

  ```bash
  bash wiki-interest/scripts/setup.sh
  ```

  Then run commands with `wiki-interest/.venv/bin/python wiki-interest/scripts/wi.py ...`.
- Internet access to `wikidata.org`, `*.wikipedia.org` and `wikimedia.org`.

Optional environment variables:

| Variable | Purpose |
|---|---|
| `WIKI_INTEREST_CONTACT` | Contact info (email or URL) added to the User-Agent, as required by the [Wikimedia User-Agent policy](https://meta.wikimedia.org/wiki/User-Agent_policy) |
| `WIKI_INTEREST_CACHE` | Cache directory (default: `wiki-interest/.cache/`) |
| `WIKI_INTEREST_DATA` | Where `fetch` writes datasets (default: `./wiki-interest-data/`) |

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

---

## Step 3: `fetch`, pageviews → dataset file

**What we did.** A command that downloads the views for a resolved topic and saves them as one dataset file.

```bash
python3 wiki-interest/scripts/wi.py fetch --qid Q333 --langs uk,pl,cs                  # last 24 complete months
python3 wiki-interest/scripts/wi.py fetch --qid Q333 --langs uk --start 2023-01 --end 2025-12
python3 wiki-interest/scripts/wi.py fetch --qid Q1666254 --langs pl,cs --article "pl=Głodówka lecznicza"
```

**Why.** Analysis needs clean, complete numbers that already take the findings from Step 1 into account. If the agent downloaded and combined the series itself, it could easily forget the redirects, use bot traffic, or mix periods. Here the rules are in code and apply every time.

**How it works:**

1. **Pick the articles.** Take the Wikidata sitelink for each language, or a proxy article passed with `--article lang=title`. A proxy is allowed only when the user approved it, and it is marked `proxy: true` everywhere. Every article is verified again on its wiki (exists, not a disambiguation page).
2. **Choose the period.** Whole months only, by default the last 24 *complete* months. An incomplete current month would look like a false drop.
3. **Download four daily series per language:**
   - `article`: human views of the article (`agent=user`, finding #6);
   - `redirects`: human views of all redirects to it, summed (capped at 40 redirects; the number skipped is reported);
   - `automated`: views flagged as automated traffic, kept as a **trust signal**, not added to the interest;
   - `project_total`: all human views of that language edition, for normalisation (finding #5).
4. **Only daily data is downloaded.** Monthly numbers are sums of days, which saves requests, and daily data is needed anyway to find spikes. Days the API does not return count as 0 views.
5. **Write a dataset file** (`wiki-interest-data/<qid>_<langs>_<start>_<end>.json`) with the full series, the period, the filters and the missing languages. It is too large for the agent's context, so **stdout gets only a compact summary**: total and average daily views, share of views from redirects, share of automated traffic, and number of zero-view days.
6. **Report missing languages** instead of dropping them silently. Status `partial` means some languages have no data, and `no_data` means none do.

**Caching.** Pageviews of past days never change, so they are cached forever. Only data for the last 3 days expires after a day. A repeated `fetch` makes 0 network requests.

**Results on real data:**

| Query | Result |
|---|---|
| `Q333` astronomy · uk,pl,cs · 24 months | `ok`. uk 23 326 views (32/day), pl 37 652 (52/day), cs 15 542 (21/day). Automated share 14–34%. 14 network requests, ~9 s |
| same, repeated | 0 network requests (17 from cache) |
| `Q1666254` intermittent fasting · pl,cs | `partial`: cs 6 939 views, pl has no article and is reported in `missing` |
| same + `--article "pl=Głodówka lecznicza"` | `ok`, pl marked `proxy: true`, and the agent is told to mention it as a limitation |
| `--end 2026-09` (current month) | `error`: only complete months are allowed |

**Cross-check with Step 1.** The totals match the independent exploration script, which used monthly data: Polish *Astronomia* 37 652 = 20 477 + 17 175 exactly, and Ukrainian *Астрономія* 23 326 vs 23 322 (the 4 extra views come from its redirect). The automated traffic for Ukrainian *Астрономія* over the last 12 months is 2 606 vs 2 612 in Step 1.

**Tests.** Period handling (last complete months, leap year, invalid months), zero-filling missing days, cache TTL, summing redirects, the redirect cap, missing languages, proxy articles, skipping disambiguation pages, and argument validation.

```bash
python3 -m unittest discover -s wiki-interest/tests -v
```

**Next step.** `analyze`: read a dataset and compute what the answer needs. That means year-over-year growth, raw and normalised by project traffic; trend direction; spike detection; seasonality; a minimum-volume threshold; and a confidence level with reasons, answering "how much can we trust this growth?".

---

## Step 4: `analyze`, dataset → growth, trend and confidence

**What we did.** A command that reads a dataset from `fetch` and computes everything the answer needs, including how much each number can be trusted.

```bash
python3 wiki-interest/scripts/wi.py analyze --dataset wiki-interest-data/Q333_uk-pl-cs_2024-09_2026-08.json
```

**Why.** This is where the question *"how much can we trust this growth?"* gets answered. A cheap model cannot reliably compute growth rates or notice that a trend comes from one odd month. So all numbers, verdicts and caveats are computed in code, and the agent only has to explain them. The output also contains ready-made **`facts`**, short sentences with the exact numbers, so the agent can quote instead of calculating.

**How it works:**

1. **Interest** = human views of the article + its redirects.
2. **Spikes.** A day is a spike if it has more than 3× the views of a 29-day rolling median *and* at least 10 extra views, so that 2 → 9 views is not a spike. Spike days are replaced by the median in a "despiked" series. Spikes in the same calendar month in different years, or in a seasonal peak month, are marked `recurring`.
3. **Normalisation.** Views per million human views of the whole language edition (finding #5).
4. **Growth, year over year.** The last 12 months are compared with the same 12 months a year earlier, so seasonality cancels out. With 13–23 months, the last *K* months are compared with the same *K* months a year earlier. Under 13 months, seasonality cannot be controlled and this is stated.
   - `raw`: absolute views, for transparency;
   - `normalized`: the **headline** number, with spikes removed, normalised, and anomalous months excluded.
5. **Anomalous months.** A whole month at least 2× its year's average that does *not* repeat in the same month of other years. Such a month and its pair a year apart are excluded from growth. This was added after real data showed a case the spike detector could not catch (see below).
6. **Consistency.** In how many of the compared months the value beat the same month a year earlier.
7. **Trend**: `rising` / `falling` if |growth| ≥ 10% and at least 2/3 of months agree; `flat` if |growth| < 10%; `mixed` if the months disagree.
8. **Seasonality.** A calendar month that is at least 1.5× its year's average on average and at least 1.3× in *every* year.
9. **Yearly breakdown** for periods of 3+ years, for questions like "over the last 5 years".
10. **Confidence** (`high` / `medium` / `low`) with explicit `reasons` and `notes`. It starts at `high` and goes down for:
    - volume under 10 views/day (→ low) or under 50 (→ at most medium);
    - under 13 months of data (→ low), or under 9 comparable months (→ at most medium);
    - an article that probably did not exist at the start of the period (→ low);
    - a proxy article (→ at most medium);
    - more than 20% of views from spikes, more than 50% automated traffic, or an inconsistent (`mixed`) change (each one level down).
11. **Comparison across languages**, ranked by normalised growth, by share per million (the topic's share of each edition's attention, comparable across editions of different sizes), and by average daily views.

The full monthly series (raw, despiked, per million) are written to `<dataset>.analysis.json` for charts. Stdout gets the compact result (~7 KB for three languages).

**What real data taught us here.** The first run reported a "seasonal peak in November" for Polish *Astronomia*. The daily numbers showed something else: **from exactly 1 to 30 November 2025**, a steady ~130 views/day, against ~38 in October and ~48 in December. A flat plateau aligned to a calendar month is most likely undetected bot traffic or a campaign. The daily spike detector missed it, because the rolling median adapts to a month-long plateau. So we added anomalous-month detection and made seasonality require a peak in *every* year. The effect on the answer is not small: Polish normalised growth went from −10.9% to **−22.8%**, because the anomaly was hiding the decline. The Ukrainian September peak (school year, finding #4) is still correctly detected as seasonal.

**Results on real data** (24 months, 2024-09..2026-08, unless noted):

| Topic · language | Headline growth (normalised) | Months up | Trend | Confidence |
|---|---|---|---|---|
| Astronomy · uk | −47.7% (raw −59.6%, whole uk.wikipedia −24.6%) | 1 / 12 | falling, seasonal peak every September | medium (18 views/day) |
| Astronomy · pl | −22.8%, Nov 2025 excluded as anomalous | 2 / 11 | falling | medium (47 views/day) |
| Astronomy · cs | −24.7% | 2 / 12 | falling | medium (17 views/day) |
| Large language model · uk | −5.5% (raw −28.4%) | 5 / 12 | **flat**: the drop is the whole Wikipedia shrinking | medium |
| Large language model · pl | +21.0% | 10 / 12 | rising | **high** |
| Large language model · de | +22.1% | 11 / 12 | rising | **high** |
| Intermittent fasting · pl (proxy *Głodówka lecznicza*) | −32.9% | 2 / 12 | falling | low (6 views/day, proxy) |
| Astronomy · uk · 60 months | per million: 40.6 → 32.5 → 30.9 → 18.1 → 9.7 (−76% over 5 years) | | falling | medium |
| Astronomy · uk · 8 months | −6.4% vs the previous 4 months | | flat | low (seasonality not controlled) |

**Tests.** Synthetic datasets with known answers:
- steady growth → `rising`, `high`;
- a shrinking Wikipedia → raw falling but `flat`;
- a one-day spike does not fake growth;
- a month-long plateau is excluded as anomalous;
- a repeating month is seasonal, not anomalous;
- low volume, short periods, proxies and bots lower confidence;
- inconsistent change → `mixed`;
- yearly breakdown for long periods;
- invalid dataset files are rejected.

```bash
python3 -m unittest discover -s wiki-interest/tests -v
```

**Next step.** `chart` and `report`: render the monthly series from `*.analysis.json` as a chart in code, and build a one-page PDF that combines the chart, a metrics table, the agent's text, and a *Method & limitations* block.

---

## Step 5: `chart` and `report`, analysis → charts and a one-page PDF

**What we did.** Two commands that turn an analysis into something a founder can look at and share.

```bash
bash wiki-interest/scripts/setup.sh   # once: creates wiki-interest/.venv with pinned matplotlib + fpdf2
PY=wiki-interest/.venv/bin/python
$PY wiki-interest/scripts/wi.py chart  --analysis wiki-interest-data/Q333_uk-pl-cs_2024-09_2026-08.analysis.json --lang uk
$PY wiki-interest/scripts/wi.py report --analysis wiki-interest-data/Q333_uk-pl-cs_2024-09_2026-08.analysis.json \
                                       --content content.json --lang uk
```

**Why.** The task asks for charts and short reports that can be shared, for example a one-page PDF. A chart drawn by the agent would look different every time and could misplot numbers. Here the chart is rendered by code from the same numbers as the analysis. For the report, the split from the design principle applies again:
- the **agent writes only what needs judgment**: title, summary, recommendations;
- **everything factual is generated**: the chart, the metrics table, and the method & limitations block.

This way the limitations cannot be forgotten or softened.

**`chart`** writes two PNGs next to the analysis file:

- **trend**: monthly share of attention (views per million, spikes removed), one line per language, on **one y-axis**. Share per million is comparable across editions of different sizes; raw views are not. Anomalous excluded months are shown as hollow circles. With more than 4 languages, the chart switches to small multiples with a shared y-axis, so lines never tangle.
- **growth**: headline year-over-year change per language with its confidence level. The colour follows the *verdict*: blue = rising, red = falling, grey = flat or mixed. So a "flat" −5% is not painted as a decline.

Chart design follows a data-visualisation checklist. Colours come from a fixed-order categorical palette that passed a colour-blindness validator (adjacent pairs distinguishable under protan/deutan/tritan simulation). Three of its colours are below 3:1 contrast on white, so every line also has a **direct label** and the report has a **table** with the same numbers. Colour never carries meaning alone.

**`report`** builds an A4 PDF with:

1. title, topic (with Wikidata ID), languages, period, date;
2. the user's question and the agent's summary;
3. the trend chart;
4. a metrics table (views/day, share per million, share change, raw change, months up, trend, confidence), with article titles linked to Wikipedia;
5. the agent's recommendations (1–5);
6. **Method & limitations**, generated from the analysis:
   - data source and filters;
   - what normalisation means;
   - how change is compared;
   - excluded anomalous months;
   - exact articles and proxies;
   - languages without data;
   - confidence with reasons;
   - the reminder that pageviews show curiosity, not willingness to pay.

The agent passes its text as JSON (a file, or `-` for stdin):

```json
{"title": "...", "question": "...", "summary": "...", "recommendations": ["...", "..."]}
```

Headings, the table and the method block are available in English and Ukrainian (`--lang en|uk`). Confidence reasons are emitted by `analyze` as codes with parameters (`reason_codes`), so they are translated too, not left in English. Fonts are the DejaVu fonts bundled with matplotlib, which cover Cyrillic and Latin with diacritics, so no font files need to be shipped.

**Checks built into `report`:**

- **Numbers in the agent's text are verified against the analysis.** Every number in the title, summary and recommendations must match a number in the analysis, within honest rounding: 47.7 → "48%" or "47,7%" is fine, "47.2%" is not. Dates and small counts are ignored. Unmatched numbers give status `needs_review` with a list, and the agent is told to fix them. This directly supports the task's requirement that reports are based on data.
- **One page, guaranteed.** If the text does not fit, the PDF is deleted and the agent is asked to shorten it.
- **Content validation**: required fields and length limits, with clear error messages.

**What we checked by looking at the output.** Each chart and the PDF were rendered and inspected, not only tested. This caught:
- a wasted band under the chart title;
- an empty half in the growth chart when all values are negative;
- a "flat" −5.5% painted red;
- a table heading broken mid-word;
- justified text with stretched spaces;
- English confidence reasons inside a Ukrainian report.

All of these were fixed. In a test report about astronomy we deliberately wrote one invented number ("35%") among real ones in several formats. The check flagged exactly that one.

**Reproducible environment.** Dependencies are pinned in `requirements.txt` and installed only inside `wiki-interest/.venv` by `scripts/setup.sh`. The environment was rebuilt from scratch with this script and all tests passed. If the dependencies are missing, `chart` and `report` return a clear error that says to run the setup script.

**Tests.** The number check (formats, invented numbers, honest rounding); a one-page PDF with charts; small multiples for 6 languages; `needs_review` for an invented number; rejection of text that does not fit on one page; and content validation. With the system Python (no dependencies), these tests are skipped and the other tests still run.

```bash
wiki-interest/.venv/bin/python -m unittest discover -s wiki-interest/tests -v
```

**Next step.** `SKILL.md`: the instructions that tie the commands into one workflow for the agent. When to ask the user (ambiguous topic, missing languages, proxies), how to read statuses, confidence and `facts`, and the rule that every claim must come from the analysis. Then an end-to-end check on a cheap model with the three example questions from the task.

---

## Step 6: `SKILL.md` and the one-call `study` command

**What we did.** Wrote [`SKILL.md`](wiki-interest/SKILL.md), the file that turns the scripts into an [Agent Skill](https://agentskills.io/specification), plus two reference files. We also added a `study` command that runs the usual path in one call.

**Why.** Until now the commands worked, but nothing told an agent *when* to use them, *in which order*, *when to stop and ask the user*, or *how to read the output*. That is what `SKILL.md` is for. It follows the Agent Skills guides ([best practices](https://agentskills.io/skill-creation/best-practices), [using scripts](https://agentskills.io/skill-creation/using-scripts), [optimizing descriptions](https://agentskills.io/skill-creation/optimizing-descriptions)). The skill must work on a cheap model, so every design choice aims at fewer decisions and fewer tool calls for the agent.

**`study`: one call instead of four.**

```bash
cd wiki-interest
.venv/bin/python scripts/wi.py study --topic "астрономія" --langs uk,pl,cs --lang uk
.venv/bin/python scripts/wi.py study --qid Q333 --langs uk,pl,cs,sk --months 36   # follow-up, all cached
```

It runs resolve → fetch → analyze → chart and **stops only when a human decision is needed**:
- `needs_user`: an ambiguous topic or one that was not found; nothing is fetched until the user picks a meaning;
- `partial`: some languages have no article; the output includes the search hits so the agent can offer a proxy.

The single steps are still available for debugging or unusual flows. The guides say agents waste steps when there are "too many options without a clear default". `study` is that default.

**How `SKILL.md` is built:**

- **Frontmatter.**
  - `name` matches the directory.
  - The `description` (603 of 1024 characters) is written as when to use the skill, in terms of the user's intent: which topics or courses to build next, which languages or markets to launch in, whether interest is growing or seasonal, how much a trend can be trusted, "even if they do not mention Wikipedia". It also says what the skill is *not* for (editing Wikipedia, web search, revenue forecasts).
  - `compatibility` states Python 3.11+, bash, the internet hosts, and that commands run from the skill directory.
- **Body** (~150 lines, ~2k tokens; the spec recommends under 500 lines and 5k tokens):
  - **Setup once**, then one exact way to run commands.
  - **A workflow checklist** (parameters → `study` → status → answer → report).
  - **A status table**: what to do for `ok`, `needs_user`, `partial`, `no_data`, `error`, including exactly how to rerun with `--qid` or `--article`.
  - **An answer template** for chat: short answer, what the data shows, how much to trust it, limitations, next step.
  - **A validation loop for the PDF**: `report` → if `needs_review`, replace the unverified numbers → rerun until `ok`.
  - **How to read the results**: which number is the headline, what each trend means, which rankings are comparable across languages.
  - **Gotchas**, which the guides call the highest-value content. Each one comes from something we actually ran into:
    - activity-style topics without articles ("learning English");
    - one article ≠ the whole topic;
    - "Меркурій" and other ambiguous terms;
    - language codes vs country codes (`cs` not `cz`, `uk` not `ua`);
    - reusing `--qid` so follow-ups stay on the same concept;
    - small numbers;
    - a shrinking Wikipedia;
    - comparing several topics.
- **Progressive disclosure.** Details the agent needs only sometimes are in `references/`, with an explicit trigger for each:
  - [`methodology.md`](wiki-interest/references/methodology.md): *read when the user asks how a number is computed or challenges a result*. It holds all definitions, thresholds, confidence rules, and how to explain them to a non-technical user.
  - [`examples.md`](wiki-interest/references/examples.md): *read for multi-language comparisons, "which audiences next" or proxies*. It gives procedures for the three example requests from the task. The procedures contain no numbers, so the agent cannot copy stale results.

**CLI polish for agents.** `--help` now starts with the usual path and documents the exit codes: 0 = done (read `status`), 1 = error (read `error`), 2 = invalid arguments. Output is indented less to save context.

**Validation.** The skill passes the official validator:

```bash
skills-ref validate wiki-interest     # -> Valid skill: wiki-interest
```

`skills-ref` is installed from [agentskills/agentskills](https://github.com/agentskills/agentskills/tree/main/skills-ref).

**Tests.** `study` stops before fetching anything when the topic is ambiguous; runs the full path with a missing language and returns search hits for a proxy; and with `--qid` skips the topic search. Writing these tests exposed a real bug: with a period too short to compare anything, the growth chart crashed on an empty list. It now returns no growth chart instead. 43 tests pass (8 are skipped without the chart/report dependencies).

**Next step.** An end-to-end check on a cheap model (Claude Haiku 4.5 or a free OpenRouter model) with the three example questions from the task. Read the agent's execution traces, and fix `SKILL.md` wherever the agent goes wrong. The guides recommend exactly this: "run the skill against real tasks, then feed the results back".
