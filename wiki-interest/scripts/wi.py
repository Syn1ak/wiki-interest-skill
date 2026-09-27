#!/usr/bin/env python3
"""wiki-interest CLI. Every command prints one JSON document to stdout.

    python3 scripts/wi.py resolve --topic "астрономія" --langs uk,pl,cs
    python3 scripts/wi.py resolve --qid Q333 --langs uk,pl,cs
    python3 scripts/wi.py fetch --qid Q333 --langs uk,pl,cs --months 24
    python3 scripts/wi.py fetch --qid Q1666254 --langs pl,cs --article "pl=Głodówka lecznicza"
"""

import argparse
import json
import sys

from wikiinterest.fetch import fetch
from wikiinterest.http import ApiError
from wikiinterest.resolve import resolve


def csv(value):
    return [v.strip().lower() for v in value.split(",") if v.strip()]


def lang_title(value):
    lang, sep, title = value.partition("=")
    if not sep or not title.strip():
        raise argparse.ArgumentTypeError("Use lang=Article title, e.g. pl=Głodówka lecznicza")
    return lang.strip().lower(), title.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="wi.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("resolve", help="Map a topic to Wikipedia articles in several languages.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--topic", help="Topic as the user wrote it, e.g. 'астрономія' or 'intermittent fasting'.")
    g.add_argument("--qid", help="Wikidata id to use directly (after the user picked a meaning), e.g. Q333.")
    p.add_argument("--langs", type=csv, required=True, help="Wikipedia language codes, e.g. uk,pl,cs.")
    p.add_argument("--search-lang", help="Language the topic text is written in (default: first of --langs).")

    p = sub.add_parser("fetch", help="Download daily views for a resolved topic into a dataset file.")
    p.add_argument("--qid", help="Wikidata id from resolve, e.g. Q333.")
    p.add_argument("--langs", type=csv, required=True, help="Wikipedia language codes, e.g. uk,pl,cs.")
    p.add_argument("--article", type=lang_title, action="append", default=[], metavar="LANG=TITLE",
                   help="Use this article for LANG instead of the Wikidata one (only a user-approved proxy). Repeatable.")
    p.add_argument("--months", type=int, default=24, help="Number of complete months ending last month (default 24).")
    p.add_argument("--start", help="First month, YYYY-MM (overrides --months).")
    p.add_argument("--end", help="Last month, YYYY-MM (default: last complete month).")
    p.add_argument("--out-dir", help="Where to write the dataset (default: ./wiki-interest-data).")

    args = parser.parse_args(argv)
    try:
        if args.command == "resolve":
            result = resolve(topic=args.topic, qid=args.qid, langs=args.langs, search_lang=args.search_lang)
        elif args.command == "fetch":
            result = fetch(qid=args.qid, langs=args.langs, overrides=dict(args.article), start=args.start,
                           end=args.end, months=args.months, out_dir=args.out_dir)
    except (ValueError, ApiError) as e:
        result = {"status": "error", "error": str(e)}

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
