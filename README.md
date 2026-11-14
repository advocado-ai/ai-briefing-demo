# ai-briefing-demo

Parts of a daily AI-engineering news briefing, built in small tested pieces: a pure-function calendar module, a feed harvester, and an
embedding-model bake-off. This repository is an excerpt of a larger working project; design documents are not included.

| Folder | What it is | Size and checks |
|---|---|---|
| [`v2/`](v2/README.md) | The calendar module: which day each feed item belongs to, and what a given day may see. Pure functions over frozen data (pydantic), no clock, no files, no network | 64 tests including property-based tests (Hypothesis), `mypy --strict`, `ruff`, and a gate script ([`v2/tools/gate.sh`](v2/tools/gate.sh)) |
| [`tools/harvest/`](tools/harvest/README.md) | Daily harvester for the RSS/Atom feeds in [`config/sources.yaml`](config/sources.yaml): fetches each feed once and keeps only items not seen before. No keys, no database, no model. Includes systemd timer units | 21 offline tests |
| [`experiments/bakeoff/`](experiments/bakeoff/README.md) | Compares five embedding-model configurations on labelled article pairs: speed, memory, model size, and how well each separates duplicate, related and unrelated pairs. The protocol is fixed before the run; raw per-pair numbers are in `results/` | 5 result files, harness with its own conda environment |

## How `v2/` was built

The calendar module was written against tests and a spec that existed first. A coding model implemented it to make them pass, and
[`v2/tools/gate.sh`](v2/tools/gate.sh) checks the result without trusting the implementer: only `v2/briefing2/` may differ from the
commit that holds the tests, the core may not import I/O or clock modules, escape-hatch comments (`# type: ignore`, `# noqa`) are
rejected, and lint, formatting, strict types and tests must all pass. The point is to catch the usual ways a coding agent "passes":
editing a test, loosening a config, or silencing the type checker.

## Run it

```bash
conda env create -f v2/environment.yml && conda activate ai-briefing-v2
v2/tools/gate.sh                      # source rules, lint, format, strict types, tests

conda env create -f tools/harvest/environment.yml && conda activate ai-briefing-harvest
pytest tools/harvest -q               # offline tests
```

Continuous integration (GitHub Actions) runs the same two commands on every push: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## 概要（日本語）

AIエンジニア向けの日次ニュース要約システムの一部を公開しています。設計文書など一部は含まれていません。

- `v2/`: 各記事がどの日に属するかを決める純粋関数のモジュール。テスト64件（Hypothesisによるプロパティテストを含む）、`mypy --strict`、`ruff`、ゲートスクリプトで検証
- `tools/harvest/`: RSS/Atomフィードを毎日収集し、新しい記事だけを保存するツール。テスト21件、systemdタイマー付き
- `experiments/bakeoff/`: 埋め込みモデル5構成を、速度・メモリ・モデルサイズ・類似記事の分離性能で比較した実験

## Licence

MIT, see [LICENSE](LICENSE).
