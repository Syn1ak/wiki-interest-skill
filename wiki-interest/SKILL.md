---
name: wiki-interest
description: >
  Measure and compare public interest in topics across languages using Wikipedia pageview
  statistics, then explain it with charts and a shareable one-page PDF report. Use this skill when
  someone asks which topics, courses, features or content to build next, which languages or
  markets to launch or localize in, whether interest in a subject is growing, falling or seasonal,
  or how much such a trend can be trusted - even if they do not mention Wikipedia. Typical users are
  founders, product managers and marketers of B2C apps. Not for editing Wikipedia, general web
  search, or revenue and sales forecasts.
compatibility: Requires Python 3.11+, bash and internet access to wikidata.org, *.wikipedia.org and wikimedia.org. Run commands from the skill directory.
metadata:
  version: "0.1"
---

# Wikipedia interest research

Wikipedia pageviews by humans are a proxy for **curiosity** about a topic in a language. They help
choose what to validate next; they do not measure willingness to pay. All numbers, verdicts and
caveats are computed by `scripts/wi.py`. Your job is to turn the request into parameters, handle
the decisions that need the user, and explain the results.

## Running commands

The shell's working directory is reset between commands, so **start every command with `cd` to this
skill's directory** (the folder that contains this SKILL.md, shown when the skill loads):

```bash
cd <skill-dir> && .venv/bin/python scripts/wi.py <command> ...
```

If `.venv` does not exist yet, run `cd <skill-dir> && bash scripts/setup.sh` once and wait for it to finish.
Every command prints one JSON document. Read its `status` and follow its `agent_hint`.

## Language

Reply in the language of the user's message: the answer, clarifying questions and short status lines.
For a Ukrainian request, write in Ukrainian and pass `--lang uk`. Copy article titles exactly as the
output gives them (e.g. *Głodówka lecznicza*); never translate or transliterate them.

## Workflow

- [ ] 1. Turn the request into parameters
- [ ] 2. Run `study`
- [ ] 3. Handle the status (ask the user when needed, rerun)
- [ ] 4. Answer in chat from `facts`
- [ ] 5. If a report/PDF/something shareable is wanted: write content, run `report`, fix until `ok`

### Stop and ask the user (end your turn with the question)

1. **Languages are not named.** For example "our chosen languages", "у вибраних нами мовних розділах" or
   "our markets". Ask which ones *before running anything*. Never pick languages yourself.
2. **`status` is `needs_user`** (ambiguous or unknown topic): show the options and ask.
3. **`status` is `partial`** (some languages have no article): ask what to do *before* answering or
   writing a report. The options are a proxy, a broader concept, or continuing without those languages.

The decision is the user's because each choice changes what the numbers mean.

### 1. Parameters

- `--topic`: the concept, as the user wrote it (any language). Add `--search-lang` with the
  language of that text if it differs from the first of `--langs`, e.g. `--search-lang en`.
- `--langs`: Wikipedia codes, comma-separated, **only the languages the user asked for**. If the user
  says "our languages" without naming them, ask which ones.
- Period: default 24 complete months. "Last two years" = `--months 24`, "five years" = `--months 60`,
  or `--start YYYY-MM --end YYYY-MM`. The current month is never included.
- `--lang uk` for Ukrainian chart and report labels, otherwise `en`.

### 2. Run

```bash
cd <skill-dir> && .venv/bin/python scripts/wi.py study --topic "астрономія" --langs uk --lang uk
```

### 3. Handle the status

| `status` | What to do |
|---|---|
| `ok` | Go to step 4. |
| `needs_user` with `resolve.status` = `ambiguous` | Show `resolve.chosen` and `resolve.alternatives` (label + description) and ask which one is meant. Rerun with `--qid <QID>` instead of `--topic`. Never pick one yourself. |
| `needs_user` with `not_found` | Retry once with the topic in English and `--search-lang en`; if still not found, ask the user to rephrase. |
| `partial` | Some languages have no article (`missing`). Say so. Offer the user: a `search_hits` title as a proxy, a broader concept, or continuing without that language. If they approve a proxy, rerun with `--qid <QID> --article LANG="Title"`. |
| `no_data` | No article in any language. Suggest a broader or related concept and run `study` again. |
| `error` | Read `error`, fix the arguments, and rerun. Rate limits are retried automatically, so do not loop. |

Follow-ups ("add Slovak", "show 5 years") are cheap because data is cached. Rerun `study` with the
same `--qid` so the concept stays the same.

### 4. Answer in chat

Quote numbers only from `facts` or `results`. Never calculate new numbers: for example, do not subtract
two growth rates ("9 points more than Poland"); give both numbers instead. Use this structure:

```markdown
**Short answer:** <one or two sentences that answer the question directly>

**What the data shows**
- <lang>: <headline change, months up, trend, from facts>
- ...

**How much to trust it:** <confidence per language + its reasons; spikes, excluded months, seasonality>

**Limitations:** <articles used (proxies marked), languages without data, pageviews = curiosity, not demand>

**Next:** <what to check or research next>
```

### 5. One-page PDF report

Write the content as JSON (a file, or pass `-` and pipe it on stdin). Everything else, including
chart, table, method and limitations, is generated.

```json
{"title": "≤100 chars",
 "question": "the user's question, ≤250 chars",
 "summary": "direct answer + key numbers + confidence, ≤900 chars",
 "recommendations": ["1-5 items, each ≤300 chars"],
 "limitations": ["optional, ≤3 items: choices you made, e.g. 'English language used instead of learning English'"]}
```

```bash
cd <skill-dir> && .venv/bin/python scripts/wi.py report --analysis <analysis path from study> --content content.json --lang uk
```

- `ok`: give the user the `pdf` path.
- `needs_review`: `unverified_numbers` are numbers in your text that are not in the analysis. Replace them
  with numbers from `facts` or remove them, then rerun. Repeat until `ok`.
- `error` "does not fit on one page": shorten the summary or recommendations, then rerun.

## Reading the results

- `growth.normalized` is the **headline change**: the topic's share of attention **within the same
  language edition** (e.g. the uk article vs all of uk.wikipedia, never vs another language), with spikes
  removed and anomalous months excluded, over the last 12 months vs the same months a year earlier.
  It already accounts for the edition shrinking: a negative value means the topic lost attention
  *beyond* Wikipedia's overall decline, so do not explain it away as "just Wikipedia falling". `growth.raw` (absolute views) is shown only to explain; `growth.project` is the change
  of the whole edition.
- `trend`: `rising` / `falling` (|change| ≥ 10% and ≥ 2/3 of months agree), `flat` (|change| < 10%),
  `mixed` (months disagree, so do not call it a trend), `unknown`.
- `confidence.level` + `confidence.reasons`: always report both. `notes` are context worth mentioning.
- `comparison.by_share_per_million`: where the topic gets the most attention, comparable across
  languages. `by_normalized_growth`: where it grows fastest. Raw views are **not** comparable across
  languages, because editions differ hugely in size.
- `seasonality`, `spikes`, `growth.excluded_months`: explain them when present, e.g. "every September
  (school year)", "a one-off spike on …", "Nov 2025 excluded as an anomaly".
- `charts.trend` / `charts.growth`: PNG files you can show or attach.

## Gotchas

- **Pageviews support research decisions, not launch decisions.** Recommend what to validate next and
  where (interviews, keyword volume, a landing-page test). Do not write "launch the course" or "ideal
  market for localization". Curiosity is not demand.
- **Do not add languages the user did not ask for.** Extra languages can be useful context, but offer them
  as a follow-up ("compare with pl and cs as well?") instead of adding them to the analysis or report.
- **Activity-style topics often have no article.** "Learning English", "вивчення англійської" or "how to
  meditate" have articles in few languages. The closest concept everywhere is usually broader
  (e.g. English language) or different (e.g. English as a second language). Show the user what exists
  and agree on the concept before comparing. State the choice in the answer and in the report's
  `limitations`.
- **One article is not the whole topic.** Interest in "astronomy" is also in *Solar eclipse*, *Mars*, and so on.
  Say that the result is about the specific article(s) used.
- **Do not trust the first meaning.** "Меркурій" is a planet, an element and a god. Only `--qid` ends the ambiguity.
- **Language codes differ from country codes:** cs (not cz), uk (not ua), sv (not se), da (not dk), el (not gr),
  ja (not jp), ko (not kr), et (not ee), sr, zh, pt, es.
- **Different wording can resolve to a different concept.** For follow-ups, reuse the `--qid` from the first run.
- **Small numbers.** Under ~10 views/day, even large percentage changes are noise. The confidence reflects
  this, so say it plainly.
- **A falling raw number is not necessarily falling interest.** Many Wikipedias are shrinking overall;
  use `growth.normalized`.
- **Data starts in 2015-07**, and only complete months are used.
- **Several topics** (e.g. astronomy vs biology): run `study` once per topic with the same `--langs` and
  period, then compare their `share_per_million` and `growth.normalized`. A report covers one topic.

## When to read more

- Read [references/methodology.md](references/methodology.md) when the user asks how a number or
  confidence level is computed, challenges a result, or wants the thresholds.
- Read [references/examples.md](references/examples.md) when unsure how to handle a comparison across
  many languages, a "which audiences next" recommendation, or a proxy article.
