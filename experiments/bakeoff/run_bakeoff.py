#!/usr/bin/env python3
"""B1b: run one embedding candidate over the labelled pairs and take the measurements fixed in README.md.

Run (from the repository root, environment ai-briefing-bakeoff):
    python experiments/bakeoff/run_bakeoff.py --list
    python experiments/bakeoff/run_bakeoff.py --candidate bge-small-384     # one candidate -> results/bge-small-384.json
    python experiments/bakeoff/run_bakeoff.py --all                         # every candidate, each in its own process
    python experiments/bakeoff/run_bakeoff.py --summary                     # tables from results/*.json

One candidate per process on purpose: peak memory is then that model's own, not the sum of the earlier ones.
CPU only. Needs data/articles.jsonl, rewrites.yaml and labels.yaml. The first run of a candidate downloads the model
(no account or key: all four repositories are ungated). The results hold ids and numbers only, no article text.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import yaml

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
FASTEMBED_CACHE = Path.home() / ".cache" / "bakeoff-fastembed"
TEXT_ONLY = {"vision_config": None, "audio_config": None}

# The format each model gets, from its card (README.md, "B1b"). The task is symmetric (article against article), so both sides of a pair,
# and both the query and the candidates in retrieval, are encoded the same way.
CANDIDATES: dict[str, dict[str, Any]] = {
    "bge-small-384": {
        "lib": "fastembed",
        "model": "BAAI/bge-small-en-v1.5",
        "dim": 384,
        "format": "no instruction on either side; fastembed's own build, the int8-quantised ONNX file Qdrant/bge-small-en-v1.5-onnx-Q",
    },
    "e5-base-768": {
        "lib": "st",
        "model": "intfloat/multilingual-e5-base",
        "dim": 768,
        "prefix": "query: ",
        "format": '"query: " on both sides (the card\'s rule for symmetric tasks)',
    },
    "gemma2-256": {
        "lib": "st",
        "model": "google/embeddinggemma-2",
        "dim": 256,
        "prompt_name": "SentenceSimilarity",
        "format": "prompt SentenceSimilarity on both sides (the card's symmetric rule); text part only; float32; cut to 256 and re-normalised",
    },
    "gemma2-768": {
        "lib": "st",
        "model": "google/embeddinggemma-2",
        "dim": 768,
        "prompt_name": "SentenceSimilarity",
        "format": "prompt SentenceSimilarity on both sides (the card's symmetric rule); text part only; float32",
    },
    "qwen3-512": {
        "lib": "st",
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "dim": 512,
        "format": "no instruction on either side (the card gives documents none); left padding as in the card; cut to 512 and re-normalised",
    },
}


# ---------------------------------------------------------------------------------------------------------------- data


def load_texts() -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """All texts (318 articles, then the 15 rewrites), their ids, and the labelled pairs."""
    articles = [json.loads(line) for line in (HERE / "data" / "articles.jsonl").read_text().splitlines()]
    rewrites = yaml.safe_load((HERE / "rewrites.yaml").read_text())["rewrites"]
    ids = [a["id"] for a in articles] + [f"rw-{r['of']}" for r in rewrites]
    texts = [f"{a['title']}\n{a['abstract']}" for a in articles] + [f"{r['title']}\n{r['abstract'].strip()}" for r in rewrites]
    pairs = yaml.safe_load((HERE / "labels.yaml").read_text())["pairs"]
    return ids, texts, pairs


# ------------------------------------------------------------------------------------------------------------- encoders


def build_encoder(spec: dict[str, Any]) -> Callable[[list[str]], np.ndarray]:
    """Load the model once; return a function from texts to unit-length vectors of the candidate's size."""
    if spec["lib"] == "fastembed":
        from fastembed import TextEmbedding

        fe = TextEmbedding(spec["model"], cache_dir=str(FASTEMBED_CACHE))

        def encode_fe(texts: list[str]) -> np.ndarray:
            return unit(np.array(list(fe.embed(texts, batch_size=32))))

        return encode_fe

    import torch
    from sentence_transformers import SentenceTransformer

    kwargs: dict[str, Any] = {"device": "cpu", "truncate_dim": spec["dim"], "model_kwargs": {"torch_dtype": torch.float32}}
    if "gemma" in spec["model"]:
        kwargs["config_kwargs"] = TEXT_ONLY
    if "Qwen3" in spec["model"]:
        kwargs["tokenizer_kwargs"] = {"padding_side": "left"}
    st = SentenceTransformer(spec["model"], **kwargs)
    st.max_seq_length = 512
    prefix = spec.get("prefix", "")
    prompt_name = spec.get("prompt_name")

    def encode_st(texts: list[str]) -> np.ndarray:
        return unit(
            st.encode([prefix + t for t in texts], prompt_name=prompt_name, batch_size=32, normalize_embeddings=True, convert_to_numpy=True)
        )

    return encode_st


def unit(vectors: np.ndarray) -> np.ndarray:
    """Scale each row to length 1 (idempotent), so cosine similarity is a dot product."""
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


# -------------------------------------------------------------------------------------------------------------- metrics


def spread(values: list[float]) -> dict[str, float | int]:
    return {"n": len(values), "min": min(values), "median": statistics.median(values), "max": max(values)}


def auc(positive: list[float], negative: list[float]) -> float:
    """Chance that a random positive scores above a random negative (ties count half). 1.0 = perfectly ordered. Scale-free."""
    wins = sum((p > n) + 0.5 * (p == n) for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


def best_threshold(same: list[float], other: list[float]) -> dict[str, float | int]:
    """The cut-off that classifies the most pairs correctly as 'same' (>= cut-off) or not, and the range of cut-offs that tie for best."""
    values = sorted(set(same + other))
    cuts = [values[0] - 1e-6] + [(a + b) / 2 for a, b in zip(values, values[1:], strict=False)] + [values[-1] + 1e-6]
    total = len(same) + len(other)
    scored = [(sum(s >= c for s in same) + sum(o < c for o in other), c) for c in cuts]
    top = max(correct for correct, _ in scored)
    winners = [c for correct, c in scored if correct == top]
    return {"accuracy": top / total, "correct": top, "of": total, "cut_low": min(winners), "cut_high": max(winners)}


def separation(pairs: list[dict[str, Any]], sims: list[float]) -> dict[str, Any]:
    by_class: dict[str, list[float]] = {"same_real": [], "same_rewritten": [], "related": [], "unrelated": []}
    for pair, sim in zip(pairs, sims, strict=True):
        name = f"same_{pair['kind']}" if pair["label"] == "same" else pair["label"]
        by_class[name].append(sim)
    same = by_class["same_real"] + by_class["same_rewritten"]
    related, unrelated = by_class["related"], by_class["unrelated"]
    return {
        "classes": {k: spread(v) for k, v in by_class.items()},
        "median_order_same_related_unrelated": statistics.median(same) > statistics.median(related) > statistics.median(unrelated),
        "gap_same_over_related": min(same) - max(related),  # > 0 means one cut-off separates them completely
        "gap_related_over_unrelated": min(related) - max(unrelated),
        "gap_same_over_all_others": min(same) - max(related + unrelated),
        "auc_same_vs_related": auc(same, related),
        "auc_same_vs_unrelated": auc(same, unrelated),
        "auc_related_vs_unrelated": auc(related, unrelated),
        "threshold_same_vs_all_others": best_threshold(same, related + unrelated),
        "threshold_same_vs_related": best_threshold(same, related),
    }


def retrieval(ids: list[str], vectors: np.ndarray, pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Rank of the partner among all other texts, for both members of each same-story pair acting as the query."""
    index = {identifier: i for i, identifier in enumerate(ids)}
    sim = vectors @ vectors.T
    ranks: dict[str, list[int]] = {"real": [], "rewritten": []}
    for pair in pairs:
        if pair["label"] != "same":
            continue
        for query, partner in ((pair["a"], pair["b"]), (pair["b"], pair["a"])):
            q, p = index[query], index[partner]
            others = np.delete(sim[q], [q, p])
            ranks[pair["kind"]].append(int(1 + (others > sim[q, p]).sum()))
    every = ranks["real"] + ranks["rewritten"]

    def summary(r: list[int]) -> dict[str, Any]:
        return {
            "queries": len(r),
            "top1": sum(x <= 1 for x in r) / len(r),
            "top5": sum(x <= 5 for x in r) / len(r),
            "mean_reciprocal_rank": sum(1 / x for x in r) / len(r),
            "worst_rank": max(r),
        }

    return {"universe": len(ids), "all": summary(every), "real": summary(ranks["real"]), "rewritten": summary(ranks["rewritten"])}


def dir_size_mb(path: Path) -> float:
    """Bytes of the real files behind `path`, in MB. A Hugging Face cache links each snapshot file to a blob (here in a shared store
    outside the model's folder), so every link is followed to its target and each target is counted once."""
    real = {f.resolve() for f in path.rglob("*") if f.is_file()}
    return sum(f.stat().st_size for f in real) / 1e6


def cache_size_mb(spec: dict[str, Any]) -> float | None:
    """What the model costs on disk: its folder in the Hugging Face cache (or fastembed's cache)."""
    if spec["lib"] == "fastembed":
        return dir_size_mb(FASTEMBED_CACHE) if FASTEMBED_CACHE.exists() else None
    from huggingface_hub import constants

    folder = Path(constants.HF_HUB_CACHE) / ("models--" + spec["model"].replace("/", "--"))
    return dir_size_mb(folder) if folder.exists() else None


# ----------------------------------------------------------------------------------------------------------------- run


def run_candidate(key: str) -> dict[str, Any]:
    import psutil

    spec = CANDIDATES[key]
    ids, texts, pairs = load_texts()
    rss_before_mb = psutil.Process().memory_info().rss / 1e6
    t0 = time.perf_counter()
    encode = build_encoder(spec)
    load_seconds = time.perf_counter() - t0

    encode(texts[:8])  # warm-up, not timed: the first call pays one-off set-up costs
    timings = []
    for _ in range(3):
        t = time.perf_counter()
        encode(texts[:100])
        timings.append(time.perf_counter() - t)

    vectors = encode(texts)
    assert vectors.shape == (len(texts), spec["dim"]), vectors.shape
    index = {identifier: i for i, identifier in enumerate(ids)}
    sims = [float(vectors[index[p["a"]]] @ vectors[index[p["b"]]]) for p in pairs]

    thread_info: dict[str, Any] = {"cpu_count": os.cpu_count()}
    if spec["lib"] == "st":
        import torch

        thread_info["torch_threads"] = torch.get_num_threads()
    return {
        "candidate": key,
        "model": spec["model"],
        "library": spec["lib"],
        "dim": spec["dim"],
        "format": spec["format"],
        "texts": len(texts),
        "load_seconds": round(load_seconds, 2),
        "seconds_per_100_runs": [round(x, 3) for x in timings],
        "seconds_per_100_median": round(statistics.median(timings), 3),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "rss_before_load_mb": round(rss_before_mb, 1),
        "model_files_mb": None if (size := cache_size_mb(spec)) is None else round(size, 1),
        "threads": thread_info,
        "separation": separation(pairs, sims),
        "retrieval": retrieval(ids, vectors, pairs),
        "pair_similarities": [
            {"a": p["a"], "b": p["b"], "label": p["label"], "kind": p.get("kind"), "sim": round(s, 4)} for p, s in zip(pairs, sims, strict=True)
        ],
    }


def summary() -> None:
    rows = [json.loads(p.read_text()) for p in sorted(RESULTS.glob("*.json"))]
    order = {k: i for i, k in enumerate(CANDIDATES)}
    rows.sort(key=lambda r: order.get(r["candidate"], 99))
    print("| candidate | size | s/100 | peak MB | files MB | AUC same>related | AUC related>unrel | clean gap same/related | best cut-off acc. | top-1 | top-5 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        s, rt = r["separation"], r["retrieval"]["all"]
        print(
            f"| {r['candidate']} | {r['dim']} | {r['seconds_per_100_median']} | {r['peak_rss_mb']:.0f} | {r['model_files_mb']} "
            f"| {s['auc_same_vs_related']:.3f} | {s['auc_related_vs_unrelated']:.3f} | {s['gap_same_over_related']:+.3f} "
            f"| {s['threshold_same_vs_all_others']['accuracy']:.3f} | {rt['top1']:.2f} | {rt['top5']:.2f} |"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--candidate", choices=list(CANDIDATES))
    group.add_argument("--all", action="store_true")
    group.add_argument("--list", action="store_true")
    group.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    if args.list:
        for key, spec in CANDIDATES.items():
            print(f"{key:15} {spec['model']} ({spec['lib']}, {spec['dim']}): {spec['format']}")
    elif args.summary:
        summary()
    elif args.all:
        for key in CANDIDATES:
            print(f"=== {key}", flush=True)
            code = subprocess.call([sys.executable, __file__, "--candidate", key])
            if code:
                print(f"    {key} FAILED with exit code {code}", flush=True)
    else:
        result = run_candidate(args.candidate)
        RESULTS.mkdir(exist_ok=True)
        (RESULTS / f"{args.candidate}.json").write_text(json.dumps(result, indent=1) + "\n")
        print(f"{args.candidate}: {result['seconds_per_100_median']} s/100, peak {result['peak_rss_mb']:.0f} MB -> results/{args.candidate}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
