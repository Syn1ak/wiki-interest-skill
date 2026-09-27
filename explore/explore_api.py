#!/usr/bin/env python3
"""Розвідувальний скрипт: руками дивимося на Wikidata + Wikimedia Pageviews API.

Це НЕ код навички, а чернетка, щоб зрозуміти дані перед проєктуванням.
Тільки стандартна бібліотека Python, без залежностей.

Приклади:
    python3 explore/explore_api.py
    python3 explore/explore_api.py --query "intermittent fasting" --search-lang en --langs pl,cs,en
"""

import argparse
import json
import os
import statistics
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

PAGEVIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
WIKIDATA = "https://www.wikidata.org/w/api.php"
OUT_DIR = Path(__file__).parent / "output"

# Wikimedia вимагає User-Agent з контактом: https://meta.wikimedia.org/wiki/User-Agent_policy
CONTACT = os.environ.get("WIKI_UA_CONTACT", "educational project")
USER_AGENT = f"wiki-interest-skill-explore/0.1 ({CONTACT})"


def get_json(url, params=None, save_as=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(5):
        time.sleep(0.5)  # не частіше ~2 запитів/с
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Too Many Requests: чекаємо і пробуємо знову
                wait = int(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                print(f"  ! HTTP 429 (ліміт запитів), чекаю {wait} с…")
                time.sleep(wait)
                continue
            print(f"  ! HTTP {e.code} для {url}")
            return None
    else:
        return None
    if save_as:
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / save_as).write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return data


def ymdh(d):
    return d.strftime("%Y%m%d") + "00"


def section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# --- 1. Тема -> кандидати у Wikidata -----------------------------------------

def search_wikidata(query, lang):
    data = get_json(WIKIDATA, {
        "action": "wbsearchentities", "search": query, "language": lang,
        "uselang": lang, "type": "item", "limit": 5, "format": "json",
    }, save_as="1_wikidata_search.json")
    return data.get("search", []) if data else []


# --- 2. QID -> назви статей різними мовами (sitelinks) -----------------------

def sitelinks(qid, langs):
    data = get_json(WIKIDATA, {
        "action": "wbgetentities", "ids": qid, "props": "sitelinks",
        "sitefilter": "|".join(f"{l}wiki" for l in langs), "format": "json",
    }, save_as="2_wikidata_sitelinks.json")
    links = data["entities"][qid].get("sitelinks", {}) if data else {}
    return {l: links[f"{l}wiki"]["title"] for l in langs if f"{l}wiki" in links}


# --- 3. Перегляди статті ------------------------------------------------------

def article_views(lang, title, start, end, granularity="monthly", agent="user"):
    article = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = (f"{PAGEVIEWS}/per-article/{lang}.wikipedia.org/all-access/{agent}/"
           f"{article}/{granularity}/{ymdh(start)}/{ymdh(end)}")
    data = get_json(url, save_as=f"3_views_{lang}_{granularity}_{agent}.json")
    return [(i["timestamp"][:8], i["views"]) for i in data["items"]] if data else []


def project_views(lang, start, end):
    url = (f"{PAGEVIEWS}/aggregate/{lang}.wikipedia.org/all-access/user/"
           f"monthly/{ymdh(start)}/{ymdh(end)}")
    data = get_json(url, save_as=f"4_project_{lang}_monthly.json")
    return {i["timestamp"][:8]: i["views"] for i in data["items"]} if data else {}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--query", default="астрономія")
    p.add_argument("--search-lang", default="uk")
    p.add_argument("--langs", default="uk,pl,cs,en")
    p.add_argument("--months", type=int, default=24)
    args = p.parse_args()
    langs = args.langs.split(",")

    # Період: останні N повних місяців
    first_of_this_month = date.today().replace(day=1)
    end = first_of_this_month - timedelta(days=1)
    m = first_of_this_month.year * 12 + first_of_this_month.month - 1 - args.months
    start = date(m // 12, m % 12 + 1, 1)

    section(f"1. Пошук «{args.query}» у Wikidata ({args.search_lang})")
    candidates = search_wikidata(args.query, args.search_lang)
    for c in candidates:
        print(f"  {c['id']:<12} {c.get('label', ''):<30} — {c.get('description', '')}")
    if not candidates:
        print("  Нічого не знайдено")
        return
    qid = candidates[0]["id"]
    print(f"\n  -> Беремо першого кандидата: {qid}. У навичці тут має бути перевірка/уточнення!")

    section(f"2. Назви статей для {qid} різними мовами")
    titles = sitelinks(qid, langs)
    for lang in langs:
        print(f"  {lang}: {titles.get(lang, '— статті немає')}")

    section(f"3. Місячні перегляди (agent=user), {start} … {end}")
    series = {lang: article_views(lang, t, start, end) for lang, t in titles.items()}
    months = sorted({m for s in series.values() for m, _ in s})
    print("  місяць    " + "".join(f"{l:>10}" for l in series))
    for m in months:
        row = "".join(f"{dict(series[l]).get(m, '-'):>10}" for l in series)
        print(f"  {m[:6]}    {row}")

    # Ріст: сума останніх 12 міс проти попередніх 12 міс
    print("\n  Ріст (останні 12 міс vs попередні 12 міс):")
    for lang, s in series.items():
        v = [x for _, x in s]
        if len(v) >= 24:
            prev, last = sum(v[-24:-12]), sum(v[-12:])
            print(f"    {lang}: {prev:>9} -> {last:>9}  ({(last - prev) / prev:+.1%})")

    section("4. Нормалізація: частка від усіх переглядів мовного розділу")
    print("  (переглядів статті на 1 млн переглядів усієї Вікіпедії цією мовою)")
    for lang, s in series.items():
        totals = project_views(lang, start, end)
        norm = [(m, v / totals[m] * 1e6) for m, v in s if m in totals]
        if len(norm) >= 24:
            prev = statistics.mean(x for _, x in norm[-24:-12])
            last = statistics.mean(x for _, x in norm[-12:])
            print(f"    {lang}: {prev:8.1f} -> {last:8.1f}  ({(last - prev) / prev:+.1%})"
                  f"   | весь розділ: {sum(list(totals.values())[-12:]) / sum(list(totals.values())[-24:-12]) - 1:+.1%}")

    first_lang, first_title = next(iter(titles.items()))

    section(f"5. Хто дивиться: люди vs боти ({first_lang}: {first_title}, останні 12 міс)")
    year_ago = end - timedelta(days=365)
    for agent in ("user", "automated", "spider"):
        total = sum(v for _, v in article_views(first_lang, first_title, year_ago, end, agent=agent))
        print(f"    {agent:<10} {total:>10}")

    section(f"6. Денні перегляди і сплески ({first_lang}: {first_title}, останні 365 днів)")
    daily = article_views(first_lang, first_title, year_ago, end, granularity="daily")
    if not daily:
        print("  Немає даних")
        return
    med = statistics.median(v for _, v in daily)
    print(f"  Медіана: {med:.0f} переглядів/день")
    spikes = [(d, v) for d, v in daily if v > 3 * med]
    print(f"  Днів, коли переглядів > 3× медіани: {len(spikes)}")
    for d, v in sorted(spikes, key=lambda x: -x[1])[:10]:
        print(f"    {d[:4]}-{d[4:6]}-{d[6:]}: {v:>7}  ({v / med:.1f}× від звичайного)")

    print(f"\nСирі відповіді API збережено в {OUT_DIR.relative_to(Path.cwd()) if OUT_DIR.is_relative_to(Path.cwd()) else OUT_DIR}")


if __name__ == "__main__":
    main()
