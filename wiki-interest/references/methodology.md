# Methodology

Use this when the user asks how something is computed or questions a result. All thresholds
live as constants at the top of `scripts/wikiinterest/analyze.py`.

## Data

- Source: Wikimedia Pageviews API, **daily** views, `agent=user` (humans; known bots and crawlers
  excluded), `access=all-access` (desktop + mobile web + app).
- Topic → article: Wikidata sitelinks give the exact article in each language. The article is
  verified on its wiki: it must exist and must not be a disambiguation page.
- **Redirects**: the API counts views of a redirect (e.g. *Hvězdářství* → *Astronomie*) separately,
  so up to 40 redirects per article are fetched and added.
- `automated` views are fetched as a **trust signal only**: a high share hints that some bots may
  still be counted as users. They are never added to interest.
- Whole-edition views (`project_total`) are used for normalisation.
- Only complete months are used. Past data is cached forever, and the last 3 days are refreshed daily.

## Metrics

| Metric | Definition |
|---|---|
| Interest | human views of the article + its redirects |
| Spike | a day with > 3× the 29-day rolling median **and** ≥ 10 extra views. Replaced by the median in the "despiked" series |
| Share per million | despiked views / whole-edition views × 1,000,000. Comparable across languages |
| Anomalous month | a whole month ≥ 2× its year's average that is **not** ≥ 1.3× in the same month of other years. Catches month-long plateaus (bots, campaigns) that the daily spike rule misses |
| Headline change (`growth.normalized`) | share per million, last K months vs the same K months a year earlier (K = 12 with 24+ months). Pairs with an anomalous month are dropped |
| Raw change (`growth.raw`) | absolute views, same windows, nothing removed |
| Consistency | months (of the compared pairs) above the same month a year earlier |
| Seasonality | calendar month with an average ≥ 1.5× its year's average and ≥ 1.3× in every year |
| Yearly | share per million for each complete 12-month block (periods of 3+ years) |

With fewer than 13 months, the last half is compared with the first half. **Seasonality is not
controlled** in that case, and confidence is low.

## Trend

- `flat`: |headline change| < 10%
- `rising` / `falling`: |change| ≥ 10% and at least 2/3 of the compared months agree in direction
- `mixed`: |change| ≥ 10% but the months disagree. It comes from a few months, so it is not a trend.

## Confidence

The level starts at **high** and is lowered:

| Condition | Effect |
|---|---|
| < 10 views/day (last 12 months) | → low |
| < 50 views/day | at most medium |
| < 13 months of data | → low (seasonality not controlled) |
| < 9 comparable month pairs | at most medium |
| no views in the first 30+ days (article probably created during the period) | → low |
| proxy article | at most medium |
| > 20% of views from spikes | one level down |
| > 50% of traffic automated | one level down (> 30% is only a note) |
| `mixed` trend | one level down |

## How to explain it to a non-technical user

- "Share of attention" = how many out of every million Wikipedia page views in that language went to the article.
- "Compared with the same months last year" = seasonal effects such as the school year or New Year's resolutions cancel out.
- "Spikes removed" = one-day bursts (news, a viral post, a TV mention) do not count as lasting interest.
- Pageviews measure curiosity, not purchase intent. Treat them as a way to decide what to research
  next (interviews, landing-page tests, keyword research), not as a demand forecast.
