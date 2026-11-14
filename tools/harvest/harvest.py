#!/usr/bin/env python3
"""Daily feed harvester: keeps the articles the feeds publish while the new system is still being built.

Run (from the repository root, environment ai-briefing-harvest):
    python tools/harvest/harvest.py              # fetch every feed once, append what is new
    python tools/harvest/harvest.py --status     # when did it last work, and how much arrived lately

Reads the feed list in config/sources.yaml (RSS and Atom only; the html_index entries are skipped), fetches each feed once, and appends
every item not seen before to tools/harvest/data/items.jsonl. It writes one line per feed and one per run to data/runs.jsonl. The data
folder is git-ignored on purpose: it holds other publishers' text. No keys, no database, no model: embedding and scoring happen later,
in the back-dated run (REQ-51), so collecting the raw text now loses nothing.

Layout: a pure core (clean, parse_feed, merge_new, last_success, feeds_at_risk) and a thin shell (main). "Now" is an argument of the core.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import feedparser
import httpx
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # the repository root
USER_AGENT = "ai-briefing-harvest/0.1 (personal feed reader)"
ABSTRACT_LIMIT = 5000


@dataclass(frozen=True)
class Item:
    """One article as a feed published it. The id is provisional: the archive recomputes it from the canonical URL."""

    id: str
    source: str
    hint: str | None
    title: str
    link: str
    abstract: str
    published: str | None  # UTC, "2026-11-11T08:00:00Z"; None if the feed gave no date
    first_seen_at: str  # UTC, when this harvester first saw it
    announce_type: str | None = None  # arXiv only: "new" or "cross" (replacements are dropped); None for every other feed


@dataclass(frozen=True)
class FeedResult:
    entries_seen: int  # everything the feed listed, before any filter
    items: tuple[Item, ...]  # after the keyword filter, the window and duplicate links
    oldest_published: str | None  # of everything the feed listed
    build_date: str | None  # the feed's own "last updated", if it says
    skip_days: tuple[str, ...] = ()  # days the feed says it is empty on (arXiv: Saturday, Sunday)


# --------------------------------------------------------------------------------------------- core (pure)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def clean(text: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def item_id(link: str) -> str:
    return hashlib.sha1(link.encode()).hexdigest()[:10]


def _moment(entry: Mapping[str, Any]) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(parsed), UTC) if parsed else None


ARXIV_PREFIX = re.compile(r"^arXiv:\S+ Announce Type: (\S+) Abstract: ")
KEPT_ANNOUNCE_TYPES = ("new", "cross")  # replace and replace-cross re-announce old papers, so they are not new articles
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _abstract(entry: Mapping[str, Any]) -> tuple[str, str | None]:
    """The cleaned abstract, and the arXiv announce type when the text starts with arXiv's prefix (which is removed)."""
    text = entry.get("summary") or entry.get("description") or ""
    if not text and entry.get("content"):
        text = entry["content"][0].get("value", "")
    text = clean(text)
    found = ARXIV_PREFIX.match(text)
    if found:
        return text[found.end() :][:ABSTRACT_LIMIT], found.group(1)
    return text[:ABSTRACT_LIMIT], None


def skip_days(content: bytes) -> tuple[str, ...]:
    """The weekday names in the feed's own <skipDays> element. feedparser flattens that element, so read the bytes."""
    block = re.search(rb"<skipDays>(.*?)</skipDays>", content, re.S | re.I)
    if not block:
        return ()
    names = re.findall(rb"<day>\s*([A-Za-z]+)\s*</day>", block.group(1), re.I)
    return tuple(sorted(n.decode().capitalize() for n in names if n.decode().capitalize() in WEEKDAYS))


def expected_empty(result: FeedResult) -> bool:
    """An empty feed is normal when its own build date falls on a day the feed says it skips (arXiv on Saturday and Sunday)."""
    if result.entries_seen > 0 or result.build_date is None:
        return False
    return WEEKDAYS[parse_iso(result.build_date).weekday()] in result.skip_days


def parse_feed(content: bytes, source: Mapping[str, Any], now: datetime, since_days: int) -> FeedResult:
    """The items of one fetched feed. Malformed bytes give an empty result, never an exception."""
    parsed = feedparser.parse(content)
    keywords = tuple(k.lower() for k in source.get("keywords", []))
    cutoff = now - timedelta(days=since_days)
    seen: set[str] = set()
    moments: list[datetime] = []
    items: list[Item] = []
    for entry in parsed.entries:
        moment = _moment(entry)
        if moment is not None:
            moments.append(moment)
        link = entry.get("link", "")
        title = clean(entry.get("title", ""))
        abstract, prefix_type = _abstract(entry)
        announce_type = entry.get("arxiv_announce_type") or prefix_type
        if not link or not title or link in seen:
            continue
        if announce_type is not None and announce_type not in KEPT_ANNOUNCE_TYPES:
            continue
        if keywords and not any(k in f"{title} {abstract}".lower() for k in keywords):
            continue
        if moment is not None and moment < cutoff:
            continue
        seen.add(link)
        items.append(
            Item(
                id=item_id(link),
                source=source["name"],
                hint=source.get("hint"),
                title=title,
                link=link,
                abstract=abstract,
                published=iso(moment) if moment else None,
                first_seen_at=iso(now),
                announce_type=announce_type,
            )
        )
    built = parsed.feed.get("updated_parsed") if parsed.feed else None
    return FeedResult(
        entries_seen=len(parsed.entries),
        items=tuple(items),
        oldest_published=iso(min(moments)) if moments else None,
        build_date=iso(datetime.fromtimestamp(calendar.timegm(built), UTC)) if built else None,
        skip_days=skip_days(content),
    )


def merge_new(known_links: Collection[str], items: Iterable[Item]) -> tuple[Item, ...]:
    """The items whose link is not stored yet. The first sighting wins: a later edit of a title is ignored."""
    seen = set(known_links)
    fresh: list[Item] = []
    for item in items:
        if item.link not in seen:
            seen.add(item.link)
            fresh.append(item)
    return tuple(fresh)


def last_success(run_lines: Iterable[Mapping[str, Any]]) -> datetime | None:
    """When the last run that reached at least one feed started."""
    times = [parse_iso(r["run_at"]) for r in run_lines if r.get("kind") == "run" and r.get("feeds_ok", 0) > 0]
    return max(times) if times else None


def feeds_at_risk(results: Mapping[str, FeedResult], since: datetime) -> tuple[str, ...]:
    """Feeds whose oldest listed entry is newer than `since`: they may have dropped items between that moment and now."""
    return tuple(
        sorted(
            name
            for name, r in results.items()
            if r.entries_seen > 0 and r.oldest_published is not None and parse_iso(r.oldest_published) > since
        )
    )


# --------------------------------------------------------------------------------------------- shell


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    if text:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)


def status(data_dir: Path, now: datetime) -> int:
    runs = read_jsonl(data_dir / "runs.jsonl")
    items = read_jsonl(data_dir / "items.jsonl")
    last = last_success(runs)
    if last is None:
        print("No successful run recorded yet.")
    else:
        print(f"Last successful run: {iso(last)} ({(now - last).total_seconds() / 3600:.1f} hours ago)")
    print(f"Runs recorded: {sum(1 for r in runs if r.get('kind') == 'run')}; items stored: {len(items)}")
    per_day = Counter(i["first_seen_at"][:10] for i in items)
    for day in sorted(per_day)[-7:]:
        print(f"  first seen {day}: {per_day[day]}")
    last_feeds = [r for r in runs if r.get("kind") == "feed" and last is not None and r["run_at"] == iso(last)]
    for r in last_feeds:
        if (r.get("empty") and not r.get("expected_empty")) or r.get("status") == "error":
            print(f"  attention: {r['source']}: {'empty feed' if r.get('empty') else r.get('error')}")
    return 0


def load_sources(path: Path) -> list[dict[str, Any]]:
    return list(yaml.safe_load(path.read_text(encoding="utf-8"))["sources"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since-days", type=int, default=30, help="keep items published in the last N days, plus undated ones")
    parser.add_argument("--data-dir", type=Path, default=HERE / "data")
    parser.add_argument("--sources", type=Path, default=ROOT / "config" / "sources.yaml")
    parser.add_argument("--status", action="store_true", help="print the harvest status and exit")
    parser.add_argument("--no-notify", action="store_true", help="never send a desktop notification")
    args = parser.parse_args()
    if args.since_days < 1:
        parser.error("--since-days must be at least 1")

    now = datetime.now(UTC).replace(microsecond=0)
    if args.status:
        return status(args.data_dir, now)

    previous = last_success(read_jsonl(args.data_dir / "runs.jsonl"))
    known = {i["link"] for i in read_jsonl(args.data_dir / "items.jsonl")}
    results: dict[str, FeedResult] = {}
    feed_lines: list[dict[str, Any]] = []
    all_items: list[Item] = []
    failed = skipped = 0

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True) as client:
        for src in load_sources(args.sources):
            name = src["name"]
            line: dict[str, Any] = {"kind": "feed", "run_at": iso(now), "source": name}
            if src.get("type", "rss") != "rss":
                skipped += 1
                feed_lines.append({**line, "status": "skipped", "error": f"type {src['type']}"})
                continue
            try:
                response = client.get(src["url"])
                response.raise_for_status()
            except httpx.HTTPError as exc:
                failed += 1
                http = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                feed_lines.append({**line, "status": "error", "http": http, "error": type(exc).__name__})
                print(f"FAILED  {name}: {type(exc).__name__}")
                time.sleep(0.5)
                continue
            result = parse_feed(response.content, src, now, args.since_days)
            results[name] = result
            all_items.extend(result.items)
            feed_lines.append(
                {
                    **line,
                    "status": "ok",
                    "http": response.status_code,
                    "entries_seen": result.entries_seen,
                    "kept": len(result.items),
                    "oldest_published": result.oldest_published,
                    "build_date": result.build_date,
                    "empty": result.entries_seen == 0,
                    "expected_empty": expected_empty(result),
                }
            )
            time.sleep(0.5)

    fresh = merge_new(known, all_items)
    new_by_source = Counter(i.source for i in fresh)
    for line in feed_lines:
        line["new"] = new_by_source.get(line["source"], 0) if line["status"] == "ok" else 0
        if line["status"] == "ok":
            flag = ("empty " if line["expected_empty"] else "EMPTY ") if line["empty"] else "ok    "
            print(f"{flag}  {line['source']}: {line['entries_seen']} listed, {line['kept']} in window, {line['new']} new")
    ok = len(results)
    append_jsonl(args.data_dir / "items.jsonl", (asdict(i) for i in fresh))
    append_jsonl(
        args.data_dir / "runs.jsonl",
        [
            *feed_lines,
            {
                "kind": "run",
                "run_at": iso(now),
                "feeds_ok": ok,
                "feeds_failed": failed,
                "feeds_skipped": skipped,
                "new_items": len(fresh),
                "since_days": args.since_days,
            },
        ],
    )
    print(f"\n{len(fresh)} new items from {ok} feeds ({failed} failed, {skipped} skipped) -> {args.data_dir / 'items.jsonl'}")

    if previous is None:
        print("First recorded run.")
    else:
        risky = feeds_at_risk(results, previous)
        if risky:
            gap = (now - previous).total_seconds() / 3600
            message = f"{gap:.0f} hours since the last good run; these feeds may have dropped items meanwhile: {', '.join(risky)}"
            print(f"WARNING: {message}")
            if not args.no_notify and shutil.which("notify-send"):
                subprocess.run(["notify-send", "ai-briefing harvest", message], check=False)
    empty = [name for name, r in results.items() if r.entries_seen == 0 and not expected_empty(r)]
    if empty:
        print(f"WARNING: empty feeds this run: {', '.join(empty)}")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
