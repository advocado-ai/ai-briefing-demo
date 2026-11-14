#!/usr/bin/env bash
# The gate: what must be true before an implementer's work is accepted.
#
#   tools/gate.sh [BASELINE_TAG]            run every step (e.g. tools/gate.sh red/calendar-01)
#   tools/gate.sh --allowlist-only TAG      only step 1; use it first when reviewing, it needs no environment
#
# Step 1 is the control that does not trust the agent: with a baseline tag, ONLY files under
# v2/briefing2/ may differ from it. Any other change (tests, configs, this script,
# the agent instructions, specs) fails the gate, whichever tool made it. Without a tag, step 1 is skipped and says so.
# Steps 2 to 6 need the conda environment: `conda activate ai-briefing-v2`.
#
# Exit code 0 = gate passed, 1 = gate failed. Writes nothing into the repository.
set -u

V2="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(git -C "$V2" rev-parse --show-toplevel)"
REL="${V2#"$REPO"/}"                  # v2
SCOPE="$(dirname "$REL")"             # the repository root: where an implementer could do damage
ALLOWED="$REL/briefing2/"

ONLY_ALLOWLIST=0
if [ "${1:-}" = "--allowlist-only" ]; then ONLY_ALLOWLIST=1; shift; fi
BASE="${1:-}"

fail=0
step() { printf '\n== %s\n' "$1"; }
bad()  { echo "FAILED: $1"; fail=1; }

# ---------------------------------------------------------------------------------------------------
step "1. Only briefing2/ changed since the baseline"
if [ -z "$BASE" ]; then
  echo "SKIPPED: no baseline tag given (pass one, for example red/calendar-01)."
  [ "$ONLY_ALLOWLIST" -eq 1 ] && { echo "--allowlist-only needs a tag."; exit 1; }
elif ! git -C "$REPO" rev-parse --verify --quiet "$BASE^{commit}" >/dev/null; then
  bad "baseline '$BASE' not found"
else
  changed="$( { git -C "$REPO" diff --name-only "$BASE" -- "$SCOPE"; \
                git -C "$REPO" ls-files --others --exclude-standard -- "$SCOPE"; } | sort -u )"
  offenders="$(printf '%s\n' "$changed" | grep -v '^$' | grep -v "^$ALLOWED" || true)"
  if [ -n "$offenders" ]; then
    bad "files outside $ALLOWED differ from $BASE:"
    printf '  %s\n' $offenders
  else
    n="$(printf '%s\n' "$changed" | grep -c . || true)"
    echo "OK: $n changed file(s), all under $ALLOWED"
  fi
fi
if [ "$ONLY_ALLOWLIST" -eq 1 ]; then
  [ "$fail" -eq 0 ] && { echo; echo "ALLOWLIST PASSED"; exit 0; } || { echo; echo "ALLOWLIST FAILED"; exit 1; }
fi

cd "$V2" || exit 1
for tool in python3 ruff mypy pytest; do
  command -v "$tool" >/dev/null 2>&1 || { bad "'$tool' not found. Run: conda activate ai-briefing-v2"; }
done
[ "$fail" -ne 0 ] && { echo; echo "GATE FAILED (environment)"; exit 1; }

step "2. Source rules (core purity, no escape-hatch comments)"
python3 tools/check_source_rules.py || fail=1

step "3. Lint (ruff check)"
ruff check . || fail=1

step "4. Formatting (ruff format --check)"
ruff format --check . || fail=1

step "5. Types (mypy, strict)"
mypy || fail=1

step "6. Tests (pytest)"
pytest -q || fail=1

echo
if [ "$fail" -eq 0 ]; then echo "GATE PASSED"; else echo "GATE FAILED"; fi
exit "$fail"
