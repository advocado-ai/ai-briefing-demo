#!/usr/bin/env python3
"""B1a: check labels.yaml and rewrites.yaml against the fetched articles, and optionally write a readable review table.

Run (from the repository root, environment ai-briefing-bakeoff):
    python experiments/bakeoff/check_labels.py            # validate; exit 1 on problems
    python experiments/bakeoff/check_labels.py --review   # also write experiments/bakeoff/labels_review.md

Needs experiments/bakeoff/data/articles.jsonl (run fetch_abstracts.py first). Proves: every id exists; no pair is repeated or pairs an
article with itself; labels and kinds are consistent (a rewritten pair is an article and its own rewrite); every rewrite is used.
Heuristic warning (not an error): a number or capitalised word in a rewrite that its original does not contain, a possible added fact.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
# Headlines of one news thread about a named person stay out of committed files (project policy: no unverified claim about a named
# person in any document). They are shown as withheld; the local, git-ignored data/pair_candidates.txt still has them for the spot-check.
WITHHELD = {"68811e0d8b", "c2f163e5b0", "0ca5cf57ee", "90a2e60457", "a24fdfddef", "f36eb145f3", "9709ee9b29"}
LABELS = {"same", "related", "unrelated"}


def words(text: str) -> set[str]:
    """Numbers and capitalised words that do not start a sentence or headline (the likely carriers of facts), lower-cased."""
    found: set[str] = set()
    for sentence in re.split(r"(?<=[.!?:])\s+|\n", text):
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.,'-]*", sentence)
        for token in tokens[1:]:
            token = token.rstrip(".,'-")
            if token[:1].isdigit() or token[:1].isupper():
                found.add(token.lower())
    return found


def main() -> int:
    articles = {r["id"]: r for r in map(json.loads, (HERE / "data" / "articles.jsonl").read_text().splitlines())}
    rewrites = {f"rw-{r['of']}": r for r in yaml.safe_load((HERE / "rewrites.yaml").read_text())["rewrites"]}
    pairs = yaml.safe_load((HERE / "labels.yaml").read_text())["pairs"]
    bad: list[str] = []
    warn: list[str] = []
    for p in pairs:
        if not (isinstance(p["a"], str) and isinstance(p["b"], str)):
            sys.exit(f"ids must be quoted strings in labels.yaml (an all-digit id parses as a number): {p}")
    for rw in rewrites.values():
        if not isinstance(rw["of"], str):
            sys.exit(f"ids must be quoted strings in rewrites.yaml: {rw['of']!r}")

    for rid, rw in rewrites.items():
        if rw["of"] not in articles:
            bad.append(f"rewrite {rid}: original {rw['of']} is not in articles.jsonl")
            continue
        original = articles[rw["of"]]
        seen_in_original = {t.lower().rstrip(".,'-") for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9.,'-]*", original["title"] + " " + original["abstract"])}
        extra = {w for w in words(rw["title"] + "\n" + rw["abstract"]) if w not in seen_in_original}
        if extra:
            warn.append(f"rewrite {rid}: not in the original: {sorted(extra)}")

    seen: set[frozenset[str]] = set()
    used: set[str] = set()
    for p in pairs:
        a, b, label, kind = p["a"], p["b"], p["label"], p.get("kind")
        key = frozenset((a, b))
        for i in (a, b):
            if i not in articles and i not in rewrites:
                bad.append(f"pair {a}/{b}: unknown id {i}")
        if a == b:
            bad.append(f"pair {a}/{b}: pairs an article with itself")
        if key in seen:
            bad.append(f"pair {a}/{b}: repeated")
        seen.add(key)
        if label not in LABELS:
            bad.append(f"pair {a}/{b}: unknown label {label}")
        if (label == "same") != (kind is not None):
            bad.append(f"pair {a}/{b}: kind is for same pairs only, and every same pair needs one")
        if kind == "rewritten":
            used.add(b)
            if b != f"rw-{a}":
                bad.append(f"pair {a}/{b}: a rewritten pair must be an article and its own rewrite")
        elif kind == "real" and (a in rewrites or b in rewrites):
            bad.append(f"pair {a}/{b}: a real pair cannot contain a rewrite")
        elif kind not in (None, "real", "rewritten"):
            bad.append(f"pair {a}/{b}: unknown kind {kind}")
    for rid in sorted(set(rewrites) - used):
        bad.append(f"rewrite {rid} is not used by any pair")

    counts = Counter((p["label"], p.get("kind")) for p in pairs)
    print({f"{k[0]}{'/' + k[1] if k[1] else ''}": v for k, v in sorted(counts.items(), key=str)}, f"| {len(pairs)} pairs")
    for w in warn:
        print("WARNING", w)

    if "--review" in sys.argv:

        def show(i: str) -> str:
            if i in WITHHELD:
                return "*(headline withheld by policy)*"
            a = articles.get(i)
            if a:
                return f"[{a['source'][:24]}] {a['title'][:100]}"
            return f"**rewrite:** {rewrites[i]['title'][:100]}"

        lines = ["# Bake-off labels, for spot-checking", "",
                 "Generated by `check_labels.py --review` from `labels.yaml`. Check the label of each row; tell the orchestrator which to change.", "",
                 "| # | label | A | B |", "|---|---|---|---|"]
        for n, p in enumerate(pairs, 1):
            tag = p["label"] + (f" ({p['kind']})" if p.get("kind") else "")
            lines.append(f"| {n} | {tag} | {show(p['a'])} | {show(p['b'])} |")
        (HERE / "labels_review.md").write_text("\n".join(lines) + "\n")
        print("wrote labels_review.md")

    print("PROBLEMS:" if bad else "all checks passed")
    for b in bad:
        print(" -", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
