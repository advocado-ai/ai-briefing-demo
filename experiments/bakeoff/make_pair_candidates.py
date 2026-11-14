#!/usr/bin/env python3
"""B1a: propose article pairs for the bake-off, WITHOUT using any of the models being compared.

Run (from the repository root, environment ai-briefing-bakeoff):  python experiments/bakeoff/make_pair_candidates.py

Candidates are chosen by plain word overlap (TF-IDF cosine over title + abstract), so the choice cannot favour one embedding model:
  same     the most word-overlapping pairs from DIFFERENT sources (likely the same story);
  related  middling overlap, same topic hint, different sources (likely the same subject, a different story);
  unrelated  near-zero overlap and different topic hints.
A person (or the orchestrator, then the owner) reads each candidate and gives the real label in labels.yaml. Limitation: a same-story pair
told in completely different words has low overlap and will not be proposed. Seeded, so the output is reproducible.
Writes experiments/bakeoff/data/pair_candidates.json (git-ignored) and prints the candidates.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

HERE = Path(__file__).resolve().parent
rows = [json.loads(line) for line in (HERE / "data" / "articles.jsonl").read_text().splitlines()]
texts = [f"{r['title']}. {r['title']}. {r['abstract']}" for r in rows]
tfidf = TfidfVectorizer(stop_words="english", sublinear_tf=True, min_df=2).fit_transform(texts)
sim = (tfidf @ tfidf.T).toarray()
np.fill_diagonal(sim, -1.0)

n = len(rows)
pairs = [(i, j) for i in range(n) for j in range(i + 1, n) if rows[i]["source"] != rows[j]["source"]]
rng = random.Random(0)

same = sorted(pairs, key=lambda p: -sim[p])[:70]
mid = [p for p in pairs if 0.07 <= sim[p] <= 0.2 and rows[p[0]]["hint"] == rows[p[1]]["hint"]]
related = rng.sample(mid, min(60, len(mid)))
low = [p for p in pairs if sim[p] < 0.01 and rows[p[0]]["hint"] != rows[p[1]]["hint"]]
unrelated = rng.sample(low, 40)

out = {"same": same, "related": related, "unrelated": unrelated}
(HERE / "data" / "pair_candidates.json").write_text(
    json.dumps({k: [[rows[i]["id"], rows[j]["id"], round(float(sim[i, j]), 3)] for i, j in v] for k, v in out.items()}, indent=1)
)
for kind, chosen in out.items():
    print(f"\n######## {kind} candidates: {len(chosen)}")
    for i, j in chosen:
        a, b = rows[i], rows[j]
        print(f"{a['id']} {b['id']} sim={sim[i, j]:.2f}\n  A [{a['source'][:22]}] {a['title'][:95]}\n  B [{b['source'][:22]}] {b['title'][:95]}")
