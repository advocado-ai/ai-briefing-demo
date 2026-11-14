#!/usr/bin/env python3
"""B1a: fetch real article abstracts for the embedding bake-off.

Run (from the repository root, environment ai-briefing-bakeoff):
    python experiments/bakeoff/fetch_abstracts.py [--per-feed 15] [--min-chars 120]

Reads the feed list in config/sources.yaml (RSS and Atom feeds only; the html_index entries are skipped), takes up to --per-feed of the
newest items from each, strips the markup from the summary and writes one JSON object per line to experiments/bakeoff/data/articles.jsonl.
That folder is git-ignored on purpose: it holds other publishers' text, and the labelled pairs refer to articles by id and link.
This is an experiment tool, not the production fetcher: no caching, no retries, no politeness beyond one request at a time.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from pathlib import Path

import feedparser
import httpx
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # the repository root
OUT = HERE / "data" / "articles.jsonl"
USER_AGENT = "bakeoff-fetch/0.1 (feed reader experiment)"


def clean(text: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def published(entry: feedparser.FeedParserDict) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", parsed) if parsed else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--per-feed", type=int, default=15)
    parser.add_argument("--min-chars", type=int, default=120, help="drop items whose abstract is shorter than this")
    args = parser.parse_args()

    sources = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())["sources"]
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True) as client:
        for src in sources:
            name = src["name"]
            if src.get("type", "rss") != "rss":
                print(f"skip   {name}: type {src['type']}")
                continue
            try:
                response = client.get(src["url"])
                response.raise_for_status()
            except httpx.HTTPError as exc:
                print(f"FAILED {name}: {type(exc).__name__}")
                continue
            keywords = [k.lower() for k in src.get("keywords", [])]
            kept = 0
            for entry in feedparser.parse(response.content).entries:
                link = entry.get("link", "")
                title = clean(entry.get("title", ""))
                abstract = clean(entry.get("summary", "") or entry.get("description", ""))
                if not link or not title or link in seen or len(abstract) < args.min_chars:
                    continue
                if keywords and not any(k in f"{title} {abstract}".lower() for k in keywords):
                    continue
                seen.add(link)
                rows.append(
                    {
                        "id": hashlib.sha1(link.encode()).hexdigest()[:10],
                        "source": name,
                        "hint": src.get("hint"),
                        "title": title,
                        "link": link,
                        "published": published(entry),
                        "abstract": abstract[:1500],
                    }
                )
                kept += 1
                if kept >= args.per_feed:
                    break
            print(f"ok     {name}: {kept}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    lengths = sorted(len(str(r["abstract"])) for r in rows)
    print(f"\n{len(rows)} articles from {len({r['source'] for r in rows})} feeds -> {OUT.relative_to(ROOT)}")
    if lengths:
        print(f"abstract length (characters): min {lengths[0]}, median {lengths[len(lengths) // 2]}, max {lengths[-1]}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
