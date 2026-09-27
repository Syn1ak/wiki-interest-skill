# Worked examples

Procedures for typical requests. The numbers come from the tool output at run time; never reuse
numbers from this file.

## A. Compare two languages for one topic

> "Compare the growth of interest in intermittent fasting in Polish and Czech Wikipedia over the last two years."

1. `study --topic "intermittent fasting" --search-lang en --langs pl,cs --months 24`
2. If the result is `partial` because Polish has no article: tell the user that a comparison is not possible
   as asked. Offer:
   - (a) a proxy from `missing.pl.search_hits` that is close in meaning (e.g. therapeutic fasting),
     clearly not the same concept;
   - (b) a broader concept;
   - (c) Czech only.
3. If they choose a proxy: `study --qid <QID> --langs pl,cs --article pl="<title>"`. Confidence for that
   language is capped at medium, and the proxy must be named in the answer.
4. Answer with the headline change, months up and confidence per language. Do not declare a winner
   when one side is low-confidence.

## B. "Is interest growing, and can we trust it?"

> "We're thinking of adding an astronomy course. Is interest growing in Ukrainian Wikipedia, and how much can we trust that growth?"

1. `study --topic "астрономія" --langs uk --lang uk`
2. Answer the growth question with `growth.normalized`, `trend` and `consistency`. Also give `growth.raw`
   and `growth.project` if raw and normalised differ a lot. That usually means the whole edition is shrinking.
3. The trust part is the confidence level **with its reasons**, plus seasonality (e.g. September =
   school year), spikes and excluded months.
4. Offer a longer view if useful: `study --qid Q333 --langs uk --months 60` (uses `yearly`).
5. Suggest narrower related concepts to check next (e.g. telescopes, eclipses) as separate `study` runs.

## C. "Which audiences should we research next?" (many languages + report)

> "We're building a language-learning app. Compare interest in learning English in our chosen language editions and prepare a short report."

1. Ask which languages, if they are not given.
2. `resolve --topic "English as a second or foreign language" --search-lang en --langs <langs>` shows
   where an article exists. Activity topics are often missing in many languages. Present the options:
   - (a) that concept, only where it exists;
   - (b) a broader concept present everywhere (e.g. English language);
   - (c) a proxy per language.

   Agree on one before comparing.
3. `study --qid <QID> --langs <langs> [--article ...]`
4. Rank audiences:
   - `comparison.by_share_per_million`: where the topic already gets the most attention;
   - `comparison.by_normalized_growth`: where attention grows fastest.

   Prefer languages that are high on either list **and** have medium/high confidence. Put low-confidence
   languages in "worth a closer look" rather than in the top picks.
5. Report content: the summary states the top 2-3 languages with their numbers and the reason. Each
   recommendation names a language and a concrete next research step (user interviews, keyword
   volume, a localized landing page test).
6. `report --analysis <path> --content content.json`. Fix `needs_review` numbers until the status is `ok`.
