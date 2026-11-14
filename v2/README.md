# v2: the calendar module

Pure functions that decide which calendar day each feed item belongs to and what a given day is allowed to see
([`briefing2/core/calendar.py`](briefing2/core/calendar.py)). The specification and design notes are not published in this repository;
the tests are the executable form of the spec.

## Layout

| Path | What it is |
|---|---|
| `briefing2/core/` | The functional core: pure functions over frozen data. Written by the implementing model |
| `tests/` | Example tests, property-based tests (Hypothesis) and tests for the gate itself. Written before the implementation |
| `tools/check_source_rules.py` | Static checks without importing the code: the core may not import I/O, clock or randomness modules, and no escape-hatch comments are allowed |
| `tools/gate.sh` | The gate: source rules, lint, format, strict types, tests, and (with a baseline tag) a check that only `briefing2/` changed |
| `pytest.ini`, `mypy.ini`, `ruff.toml`, `environment.yml` | Tool configuration |

## Run the gate

```bash
conda env create -f v2/environment.yml     # from the repository root
conda activate ai-briefing-v2
v2/tools/gate.sh                           # steps 2 to 6
v2/tools/gate.sh TAG                       # plus step 1: only briefing2/ may differ from TAG
v2/tools/gate.sh --allowlist-only TAG      # just step 1; needs no environment
```

Step 1 is the control that does not trust the implementer. With a baseline tag, any changed or new file outside `v2/briefing2/` fails the
gate, including tests, configuration, this script and files at the repository root.
