# Embedding bake-off (B1)

Chooses the embedding model (and so the vector size) for the archive, on our own data. Background and candidates:
the local-models design notes (not published here), section 5. It is an experiment, not product code: it lives outside
`v2/` and has its own conda environment ([environment.yml](environment.yml), name `ai-briefing-bakeoff`).

Three steps, one commit each: **B1a** data and labelled pairs (this folder, done below), **B1b** run the five candidates and take the
measurements ([run_bakeoff.py](run_bakeoff.py), numbers in [results/](results/)), **B1c** results note and the update to
the local-models design notes (not published here) and Q-14.

## B1a: what exists

| File | What | In git |
|---|---|---|
| [fetch_abstracts.py](fetch_abstracts.py) | Fetches up to 15 newest items from each RSS or Atom feed in `config/sources.yaml` (the `html_index` entries are skipped) into `data/articles.jsonl` | yes |
| `data/articles.jsonl` | 318 abstracts from 26 feeds (fetched 2026-11-12). Other publishers' text, so the folder is git-ignored | **no** |
| [rewrites.yaml](rewrites.yaml) | 15 constructed second tellings of fetched articles, written from the abstract only, with a different headline and wording | yes |
| [labels.yaml](labels.yaml) | 57 labelled pairs, by id only: 17 `same`, 20 `related`, 20 `unrelated` | yes |
| [labels_review.md](labels_review.md) | The same pairs with headlines, for the owner's spot-check. One news thread about a named person is shown as "withheld by policy" | yes |
| [make_pair_candidates.py](make_pair_candidates.py) | Proposed the candidate pairs from plain word overlap (TF-IDF), so no candidate model influenced the choice | yes |
| [check_labels.py](check_labels.py) | Validates labels and rewrites; `--review` writes the table | yes |

Re-create the data: `python experiments/bakeoff/fetch_abstracts.py` from the repository root. The feeds move on, so a re-fetch gives
different articles and the ids in `labels.yaml` will no longer exist. Keep `data/articles.jsonl` until the bake-off is finished.

## What the data can and cannot show (read before trusting a number)

- **Real cross-outlet duplicates are rare.** Only **2** of the 17 `same` pairs are real: two outlets on one news event, and one article
  published twice under two headlines (identical abstract). The other **15 are constructed** (`kind: rewritten`): the owner's feed list is
  mostly first-party blogs, so the same story rarely appears twice. Results must be reported for the two kinds **separately**. A model
  that is good at paraphrase may still be untested on real duplicates.
- **Abstracts are short.** The median is about 200 characters, because many feeds publish a snippet. Hacker News items carry no content
  (just "Article URL … Points …") and GitHub release items are a few words. Those are real inputs to the pipeline, and the pairs include
  some of them on purpose, but they test the model on almost no text. This is a finding for the design, not only for the bake-off.
- **Pair selection was lexical.** Candidates came from word overlap, so a same-story pair told in completely different words (and found
  by no one) is missing from the real pairs. The constructed pairs partly cover that.
- **The labels are one reader's judgement** (the orchestrator's), to be spot-checked. `related` is the fuzzy class: same subject, a
  different story.
- The test size is small (57 pairs). Differences of a few points between models are noise.

## B1b: the protocol, fixed in advance

**Candidates** (settings from the model cards, section 2 of the local-models design notes (not published here); the cards were read again when running):
`BAAI/bge-small-en-v1.5` at 384 (through `fastembed`); EmbeddingGemma 2, text part only, at 256 and at 768; `Qwen3-Embedding-0.6B` at 512
(these two through `sentence-transformers`); and, added on 2026-11-12 after finding it in earlier work, the
**incumbent** `intfloat/multilingual-e5-base` at 768 (`sentence-transformers`; `fastembed` lists only the large e5). All on the CPU
(the GPU is for the optional local chat model). Embedding vectors are normalised, similarity is cosine.

**Format per model.** The job is symmetric (article against article), so each model encodes **both sides of a pair, and the query and
candidates in retrieval, the same way**, using its card's rule for symmetric tasks. Each text is the headline, a newline, then the
abstract (at most 512 tokens).

| Candidate | What it receives |
|---|---|
| `bge-small-384` | no instruction. **Caveat:** `fastembed` ships its own build, the int8-quantised ONNX file `Qdrant/bge-small-en-v1.5-onnx-Q` (66 MB), not the float32 model. That is the form that would ship with the light library, so it is what is measured, but it is not exactly the model the card scores |
| `e5-base-768` | `query: ` before every text (the card's rule for symmetric tasks); float32 |
| `gemma2-256`, `gemma2-768` | the card's prompt `SentenceSimilarity` (`task: sentence similarity \| query: …`) on every text; text part only (`config_kwargs` `vision_config`/`audio_config` = `None`); float32 (the card forbids float16); 256 is the first 256 numbers, re-normalised |
| `qwen3-512` | no instruction (the card gives documents none, and it has no symmetric prompt); left padding as in the card; float32; first 512 numbers, re-normalised. **Limit:** the card says instructions help queries by 1 to 5 per cent, which this symmetric setup does not use |

**Ways the protocol was made exact before running** (all of this was in `run_bakeoff.py` before the first result was seen):
- *Retrieval:* each `same` pair is run in both directions (either member as the query), so 17 pairs give 34 queries (4 real, 30
  rewritten). The universe is the 333 texts; the query itself is excluded.
- *Speed:* a warm-up call on 8 texts is not timed; then the first 100 articles in `articles.jsonl` are encoded three times, batch size
  32, and the median is reported. Peak memory is the process's peak resident set (`ru_maxrss`) after the whole run, including loading.
  One process per candidate. The machine was not idle in the desktop sense (browser, editor), so treat the speeds as approximate.
- *Added to separation:* the area under the ranking curve (AUC: the chance a random pair of one class scores above a random pair of
  the other; 1.0 is perfect) and the "gap" (lowest `same` minus highest `related`; above zero means a single cut-off separates them
  completely). Raw cosine values are **not comparable between models** (e5 squeezes everything into a high range), so ranking measures
  decide, not the raw numbers.
- *Install weight:* the disk size of the model's own files, counted from real files only (symlinks skipped), plus the library weight
  read from the environment.

**Measurements, per candidate**
1. **Separation.** Cosine similarity of every labelled pair. Report the minimum, median and maximum per class (`same` real,
   `same` rewritten, `related`, `unrelated`), whether the classes are ordered `same > related > unrelated`, and the threshold that best
   separates `same` from everything else with the accuracy it gives. Also the range of thresholds that works.
2. **Retrieval.** For each of the 17 `same` pairs, the rank of the partner among all 333 texts (318 articles and 15 rewrites) when the
   other member is the query. Report top-1 and top-5 hit rates (this is REQ-41's measure).
3. **Speed and memory.** Seconds per 100 abstracts (after loading, median of three runs) and peak resident memory, on this machine's CPU.
4. **Install weight.** What had to be installed and its size on disk.

**Decision rule** (from the local-models design notes (not published here)): choose the **lightest** model that separates the three kinds of pair cleanly. A heavier model
has to do clearly better on this data to earn its extra weight. "Clearly better" is stated with the numbers, and because the test is small
a gap that could be noise does not count. If no model separates `related` from `same` cleanly, say so: the finding is then that the
archive needs a second-stage check, not a bigger model.

## B1b: run record (2026-11-12)

Raw numbers only; reading them and the decision are B1c. Per-pair similarities and every class's minimum, median and maximum are in
[results/](results/) (ids and numbers, no article text). Reproduce: `python experiments/bakeoff/run_bakeoff.py --all`, then `--summary`.

**Machine and versions.** Ryzen 5 5600H (12 threads), 19 GiB RAM, CPU only. Python 3.13; `torch` 2.15.0 (CPU build), `transformers`
5.22.2, `sentence-transformers` 6.3.0, `fastembed` 0.9.1, `onnxruntime` 1.31.1. `torch` used 6 threads (its default) and `fastembed`
its own default; the desktop was in normal use, so speeds are approximate.

| Candidate | Numbers | s per 100 abstracts (median of 3) | Peak memory (MB) | Model files (MB) | AUC same over related | AUC related over unrelated | Gap, lowest same minus highest related | Best single cut-off, 57 pairs | Retrieval top-1 / top-5, all 34 queries | Top-1 / top-5, real pairs (4 queries) |
|---|---|---|---|---|---|---|---|---|---|---|
| `bge-small-384` (quantised build) | 384 | **3.3** | **902** | **67** | 0.985 | **0.990** | -0.098 | 56 | **1.00 / 1.00** | **1.00 / 1.00** |
| `e5-base-768` (incumbent) | 768 | 7.4 | 1,985 | 1,135 | 0.976 | 0.978 | **-0.044** | 56 | 0.94 / 1.00 | 0.50 / 1.00 |
| `gemma2-256` | 256 | 19.0 | 4,287 | 1,521 | 0.979 | 0.970 | -0.059 | 56 | 0.94 / 1.00 | 0.50 / 1.00 |
| `gemma2-768` | 768 | 19.7 | 4,188 | 1,521 | 0.974 | 0.960 | -0.057 | 56 | 0.94 / 1.00 | 0.50 / 1.00 |
| `qwen3-512` | 512 | 40.7 | 3,856 | 1,208 | **0.991** | 0.953 | -0.141 | 56 | **1.00 / 1.00** | **1.00 / 1.00** |

- A negative gap means no single cut-off puts every `same` pair above every `related` pair. **That holds for all five models.**
- **The same one pair is the only error at every model's best cut-off:** the real two-outlet pair `68811e0d8b` with `c2f163e5b0`
  (headlines withheld by policy). All 15 constructed pairs and the identical-abstract pair are on the right side everywhere.
- Rewritten pairs are retrieved at rank 1 by every model (30 of 30 queries). The 4 queries on real pairs are where models differ.
- Install weight, from `du` on the environment: the `torch` stack (`torch` 809 MB, `transformers` 120 MB, `torchvision` 7 MB,
  `sentence-transformers` 6 MB) is about **940 MB** of libraries; the `fastembed` stack (`onnxruntime` 68 MB, `fastembed` 1 MB) is about
  **70 MB**. The whole environment, with NumPy, SciPy and scikit-learn, is 1.7 GB.

**Process notes (what went wrong on the way, so the numbers can be trusted).** `bge-small` was run once alone as a smoke test before the
others. Two faults in the harness were then fixed and **every candidate was re-run in one `--all` pass** with no metric and no format
changed: the disk-size counter followed cache symlinks and counted files several times, then, once fixed for that, did not follow the
cache's links into its shared blob store; and EmbeddingGemma 2's processor needs `torchvision` even for text only (added to
`environment.yml`). `fastembed` lists no `multilingual-e5-base`, so the incumbent goes through `sentence-transformers`. No account or key
was used: all four model repositories are ungated (Hugging Face API, 2026-11-12). Model files are in the Hugging Face cache and
`~/.cache/bakeoff-fastembed`, outside the repository.
