#!/usr/bin/env bash
#
# Selective test runner for the Kokoro ebook-reader repo.
#
#   ./scripts/test.sh fast                     curated fast subset, the default
#   ./scripts/test.sh backend [target...]      one backend file / -k pattern
#   ./scripts/test.sh unit [pattern]           frontend vitest
#   ./scripts/test.sh e2e [spec]               Playwright
#   ./scripts/test.sh changed                  tests mapped from the diff vs origin/main
#   ./scripts/test.sh full                     everything (backend + unit + e2e)
#   ./scripts/test.sh gates                     the four handoff gates
#   ./scripts/test.sh slow                     only the files `fast` excludes
#   ./scripts/test.sh --help
#
# Exit status is 0 only when every suite that ran passed. A suite that exceeds its
# timeout is reported as TIMEOUT, not FAIL: it is killed, never left running.
#
# Design notes
# ------------
# * Quiet by default. Full output goes to a log file under $TMPDIR; the terminal
#   gets one line per suite plus the failure lines and the log path on failure.
#   Use -v/--verbose to stream everything instead.
# * Every suite is wrapped in `timeout`. The backend suite can take minutes and
#   its slow files used to look like a hang; nothing here runs unbounded.
# * No file list is baked into pytest's own config: `fast` is computed from this
#   script's SLOW_TESTS block, so a new test file is picked up automatically.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
FRONTEND="$REPO_ROOT/frontend"

# ---------------------------------------------------------------------------
# Timeouts (seconds). Override any of them from the environment.
# ---------------------------------------------------------------------------
T_FAST="${T_FAST:-300}"        # fast subset: ~45 s measured, 300 s is headroom
T_BACKEND="${T_BACKEND:-300}"  # a single backend file or pattern
T_SLOW="${T_SLOW:-900}"        # the excluded slow files, run together
T_FULL_BACKEND="${T_FULL_BACKEND:-900}"
T_UNIT="${T_UNIT:-300}"
T_E2E="${T_E2E:-900}"
T_CHECK="${T_CHECK:-300}"

# ---------------------------------------------------------------------------
# SLOW_TESTS — the single place that decides what `fast` leaves out.
#
# Both files run real spaCy NLP over a whole book from ~/Documents/EBooks, once
# per test, with no fixture-level caching:
#
#   tests/test_pdf_engine.py        9 x extract_sentences(cleancodebook.pdf)
#                                   measured at 120 s per call
#   tests/test_epub_ocr_engines.py  6 x extract_sentences(cleancodebook.epub)
#                                   measured at  48 s per call
#
# That is ~2-4 minutes for the pair and is the entire reason the suite looked
# like it hung. They are correct tests, just slow; `./scripts/test.sh slow`
# runs them. Add a file here if it makes `fast` exceed ~60 s.
# ---------------------------------------------------------------------------
SLOW_TESTS=(
  "tests/test_pdf_engine.py"
  "tests/test_epub_ocr_engines.py"
)

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_RED=''; C_GREEN=''; C_YELLOW=''; C_BOLD=''; C_DIM=''; C_OFF=''
fi

VERBOSE=0
# Logs are kept after the run: on failure the printed path must still be readable
# and, under a sandboxed agent harness, each shell call can get its own private
# /tmp — so a path under /tmp is not readable from the *next* call. Logs go to
# test-results/, which is already in .gitignore. The directory is swept at the
# START of the next run instead of on exit, so the last run stays inspectable.
LOG_DIR="${TEST_LOG_DIR:-$REPO_ROOT/test-results/test-sh}"
mkdir -p "$LOG_DIR"
rm -f "$LOG_DIR"/*.log 2>/dev/null || true

# Suites that ran, as "name:status:seconds:logfile" triples.
RESULTS=()

hr() { printf '%s\n' "────────────────────────────────────────────────────────────────────"; }

say() { printf '%s\n' "$*"; }

note() { printf '%s%s%s\n' "$C_DIM" "$*" "$C_OFF"; }

die() { printf '%sError:%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 2; }

# ---------------------------------------------------------------------------
# Interpreter / toolchain discovery
#
# The backend moved from a hand-made backend/venv to a uv-managed backend/.venv
# (see backend/pyproject.toml). Both are supported so this script works before,
# during and after that migration. TEST_PY overrides the choice outright.
# ---------------------------------------------------------------------------
PY=()
PY_DESC=''

pick_python() {
  if [[ -n "${TEST_PY:-}" ]]; then
    PY=("$TEST_PY"); PY_DESC="$TEST_PY (from TEST_PY)"; return
  fi
  if [[ -x "$BACKEND/.venv/bin/python" ]]; then
    PY=("$BACKEND/.venv/bin/python"); PY_DESC="backend/.venv/bin/python"; return
  fi
  if [[ -x "$BACKEND/venv/bin/python" ]]; then
    PY=("$BACKEND/venv/bin/python"); PY_DESC="backend/venv/bin/python"; return
  fi
  if command -v uv >/dev/null 2>&1; then
    PY=(uv run --project "$BACKEND" python); PY_DESC="uv run --project backend python"; return
  fi
  die "no Python interpreter found. Expected backend/.venv, backend/venv, or uv on PATH.
     Create one with:  cd backend && uv sync"
}

# ---------------------------------------------------------------------------
# run_suite <name> <timeout> <workdir> <command...>
#
# Never aborts the script: the caller gets the aggregate summary even when an
# early suite fails. Records the result and prints one line.
# ---------------------------------------------------------------------------
run_suite() {
  local name=$1 timeout_s=$2 workdir=$3; shift 3
  local log="$LOG_DIR/${name//[^A-Za-z0-9_.-]/_}.log"
  local start end elapsed rc

  printf '%s▶ %s%s %s(timeout %ss)%s\n' "$C_BOLD" "$name" "$C_OFF" "$C_DIM" "$timeout_s" "$C_OFF"
  start=$(date +%s)

  if (( VERBOSE )); then
    set +e
    ( cd "$workdir" && timeout --foreground "$timeout_s" "$@" ) 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    set -e
  else
    set +e
    ( cd "$workdir" && timeout --foreground "$timeout_s" "$@" ) >"$log" 2>&1
    rc=$?
    set -e
  fi

  end=$(date +%s); elapsed=$(( end - start ))

  local status
  case "$rc" in
    0)   status="PASS" ;;
    124|137) status="TIMEOUT" ;;
    *)   status="FAIL" ;;
  esac

  RESULTS+=("$name|$status|$elapsed|$log")

  case "$status" in
    PASS)
      printf '  %s✔ PASS%s   %s  %s%ss%s\n' "$C_GREEN" "$C_OFF" "$name" "$C_DIM" "$elapsed" "$C_OFF"
      ;;
    TIMEOUT)
      printf '  %s⏱ TIMEOUT%s %s  %s%ss (killed at %ss — not a test failure; raise T_* if it is genuinely this slow)%s\n' \
        "$C_YELLOW" "$C_OFF" "$name" "$C_DIM" "$elapsed" "$timeout_s" "$C_OFF"
      print_failures "$log"
      ;;
    *)
      printf '  %s✘ FAIL%s   %s  %s%ss%s\n' "$C_RED" "$C_OFF" "$name" "$C_DIM" "$elapsed" "$C_OFF"
      print_failures "$log"
      ;;
  esac
  hr
}

# Only the failure lines: the caller does not need 500 lines of passing output.
print_failures() {
  local log=$1 max="${MAX_FAIL_LINES:-40}" shown

  shown=$(grep -E '^(FAILED|ERROR)[: ]|^E   |error: |ERROR: ' "$log" 2>/dev/null | head -n "$max" || true)
  if [[ -z "$shown" ]]; then
    # vitest / svelte-check / playwright do not use the pytest prefix
    shown=$(grep -E '(^|[[:space:]])(FAIL|✕|×|Error:|error TS|error )' "$log" 2>/dev/null | head -n "$max" || true)
  fi
  if [[ -n "$shown" ]]; then
    printf '%s' "$shown" | sed 's/^/     /'
    printf '\n'
  else
    # Never leave a failure unexplained: show a bounded tail of the log, minus
    # pytest's warnings-summary block (noise that buries the real message).
    sed '/^=* warnings summary/,/^-- Docs:/d' "$log" 2>/dev/null \
      | grep -v '^[[:space:]]*$' | tail -n 15 | sed 's/^/     /'
    printf '\n'
  fi

  local verdict
  verdict=$(grep -Eo '=+ .*(passed|failed|error|xfailed|skipped).* =+' "$log" 2>/dev/null | tail -1 || true)
  if [[ -z "$verdict" ]]; then
    verdict=$(grep -Eo '[0-9]+ (passed|failed|error)[^|]*' "$log" 2>/dev/null | tail -1 || true)
  fi
  if [[ -n "$verdict" ]]; then printf '     %s%s%s\n' "$C_DIM" "$verdict" "$C_OFF"; fi

  printf '     %sfull log: %s%s\n' "$C_DIM" "$log" "$C_OFF"
}

summary() {
  local failures=0 line name status secs
  hr
  printf '%sSummary%s\n' "$C_BOLD" "$C_OFF"
  for line in "${RESULTS[@]:-}"; do
    [[ -z "$line" ]] && continue
    name="${line%%|*}"; line="${line#*|}"
    status="${line%%|*}"; line="${line#*|}"
    secs="${line%%|*}"
    case "$status" in
      PASS)    printf '  %s✔ PASS%s    %-34s %ss\n' "$C_GREEN" "$C_OFF" "$name" "$secs" ;;
      TIMEOUT) printf '  %s⏱ TIMEOUT%s  %-34s %ss\n' "$C_YELLOW" "$C_OFF" "$name" "$secs"; failures=$((failures+1)) ;;
      *)       printf '  %s✘ FAIL%s    %-34s %ss\n' "$C_RED" "$C_OFF" "$name" "$secs"; failures=$((failures+1)) ;;
    esac
  done
  hr
  if (( failures == 0 )); then
    printf '%sALL SUITES PASSED%s\n' "$C_GREEN$C_BOLD" "$C_OFF"
    return 0
  fi
  printf '%s%d SUITE(S) FAILED OR TIMED OUT%s\n' "$C_RED$C_BOLD" "$failures" "$C_OFF"
  return 1
}

# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------
# pytest must run with CWD=backend: the app mounts StaticFiles("uploads") and the
# routers resolve uploads/, voices/ and exports/ relative to the process CWD.
#
# DO NOT add -q or --tb here. backend/pytest.ini already sets
# `addopts = -q --tb=short`, and pytest merges CLI flags with addopts: a second
# -q becomes -qq, which SUPPRESSES the "N passed" summary line entirely. A fully
# green run then looks like it printed nothing, and any verdict parsed from the
# log silently disappears. This runner deliberately passes neither.
#
# Build the pytest argv as an array: passing "$(pytest_cmd)" would collapse the
# interpreter and its flags into a single word ("command not found").
PYTEST=()
set_pytest_cmd() { PYTEST=("${PY[@]}" -m pytest); }

# All backend test files, excluding the ones named in SLOW_TESTS.
fast_test_files() {
  local f base skip
  for f in "$BACKEND"/tests/test_*.py; do
    [[ -e "$f" ]] || continue
    base="tests/$(basename "$f")"
    skip=0
    for s in "${SLOW_TESTS[@]}"; do
      if [[ "$base" == "$s" ]]; then skip=1; break; fi
    done
    if (( skip )); then continue; fi
    printf '%s\n' "$base"
  done
}

slow_test_files() {
  local s
  for s in "${SLOW_TESTS[@]}"; do
    if [[ -e "$BACKEND/$s" ]]; then printf '%s\n' "$s"; fi
  done
}

# Resolve a user-supplied backend target:
#   tests/test_foo.py | test_foo.py | test_foo | foo | routers/tts.py | -k expr
resolve_backend_target() {
  local t=$1 stem
  if [[ "$t" == -* ]]; then printf '%s\n' "$t"; return; fi          # pytest flag
  if [[ -f "$BACKEND/$t" ]]; then printf '%s\n' "$t"; return; fi
  if [[ -f "$BACKEND/tests/$t" ]]; then printf '%s\n' "tests/$t"; return; fi
  # Bare module name, accepted with or without the test_ prefix, so both
  # `backend test_tts_engine` and `backend tts_engine` work.
  for stem in "$t" "test_$t"; do
    if [[ -f "$BACKEND/tests/$stem.py" ]]; then printf '%s\n' "tests/$stem.py"; return; fi
  done
  # A source path (services/tts_engine.py) -> the test file named after it.
  stem="$(basename "$t" .py)"
  if [[ -f "$BACKEND/tests/test_$stem.py" ]]; then printf '%s\n' "tests/test_$stem.py"; return; fi
  # otherwise let pytest interpret it (node id, -k style substring, directory)
  printf '%s\n' "$t"
}

mode_fast() {
  pick_python; set_pytest_cmd
  say "${C_BOLD}fast${C_OFF} ${C_DIM}— curated subset, slow files excluded (${#SLOW_TESTS[@]} excluded)${C_OFF}"
  note "excluded: ${SLOW_TESTS[*]}"
  note "python:    $PY_DESC"
  mapfile -t files < <(fast_test_files)
  (( ${#files[@]} )) || die "no backend test files found under $BACKEND/tests"
  run_suite "backend fast (${#files[@]} files)" "$T_FAST" "$BACKEND" \
    "${PYTEST[@]}" "${files[@]}"
}

mode_slow() {
  pick_python; set_pytest_cmd
  say "${C_BOLD}slow${C_OFF} ${C_DIM}— only the files fast excludes${C_OFF}"
  mapfile -t files < <(slow_test_files)
  (( ${#files[@]} )) || { note "no slow test files present; nothing to do"; return 0; }
  run_suite "backend slow (${#files[@]} files)" "$T_SLOW" "$BACKEND" \
    "${PYTEST[@]}" "${files[@]}"
}

mode_backend() {
  pick_python; set_pytest_cmd
  local targets=() t
  if (( $# == 0 )); then
    say "${C_BOLD}backend${C_OFF} ${C_DIM}— whole backend suite${C_OFF}"
    note "python: $PY_DESC"
    run_suite "backend all" "$T_FULL_BACKEND" "$BACKEND" "${PYTEST[@]}" tests/
    return
  fi
  for t in "$@"; do targets+=("$(resolve_backend_target "$t")"); done
  say "${C_BOLD}backend${C_OFF} ${C_DIM}— ${targets[*]}${C_OFF}"
  note "python: $PY_DESC"
  run_suite "backend ${targets[*]}" "$T_BACKEND" "$BACKEND" "${PYTEST[@]}" "${targets[@]}"
}

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
mode_unit() {
  local pattern=("$@")
  say "${C_BOLD}unit${C_OFF} ${C_DIM}— vitest${C_OFF}"
  if (( ${#pattern[@]} )); then
    run_suite "vitest ${pattern[*]}" "$T_UNIT" "$FRONTEND" npm run test:unit -- "${pattern[@]}"
  else
    run_suite "vitest (all)" "$T_UNIT" "$FRONTEND" npm run test:unit
  fi
}

# Playwright covers only the *critical* flows: playwright.config.ts greps for
# the `@critical` tag unless E2E_ALL=1, so `npm run test:e2e` is the 32 critical
# tests and `npm run test:e2e:all` is everything. This mode just forwards; it
# never widens the default selection on its own.
mode_e2e() {
  local spec=("$@")
  say "${C_BOLD}e2e${C_OFF} ${C_DIM}— playwright, @critical flows only (needs :5173 and the API on :8000)${C_OFF}"
  note "run all 121 specs with: E2E_ALL=1 npm run test:e2e   (or: npm run test:e2e:all)"
  if (( ${#spec[@]} )); then
    # Accept a bare spec name as well as a path.
    local s out=()
    for s in "${spec[@]}"; do
      if [[ -f "$FRONTEND/$s" ]]; then out+=("$s")
      elif [[ -f "$FRONTEND/tests/$s" ]]; then out+=("tests/$s")
      elif [[ -f "$FRONTEND/tests/$s.spec.ts" ]]; then out+=("tests/$s.spec.ts")
      else out+=("$s"); fi
    done
    run_suite "playwright ${out[*]}" "$T_E2E" "$FRONTEND" npm run test:e2e -- "${out[@]}"
  else
    run_suite "playwright (critical)" "$T_E2E" "$FRONTEND" npm run test:e2e
  fi
}

mode_check() {
  say "${C_BOLD}check${C_OFF} ${C_DIM}— svelte-check${C_OFF}"
  run_suite "svelte-check" "$T_CHECK" "$FRONTEND" npm run check
}

# ---------------------------------------------------------------------------
# changed — a HEURISTIC, not a dependency analysis.
#
# Base is merge-base(HEAD, origin/main), so the diff covers the whole branch
# (including a colleague's uncommitted work — expect a large set on a busy tree).
#
# Mapping rules, strongest signal first:
#   1. a test file that was itself changed            -> run it
#   2. tests/test_<stem>.py for a changed module      -> exact name match
#   3. otherwise a textual search for the module stem, but ONLY when it lands on
#      <= TEXT_MATCH_CAP files. A module mentioned by 20 test files is broadly
#      used, and guessing from that is worse than admitting it: the runner says
#      so and points at `fast`.
#
# It cannot see indirect coverage, so a green `changed` is NOT proof the branch
# is green. Run `fast` before calling anything done.
# ---------------------------------------------------------------------------
TEXT_MATCH_CAP="${TEXT_MATCH_CAP:-3}"   # per changed module
BIG_DIFF_WARN="${BIG_DIFF_WARN:-15}"    # suggest `fast` past this many backend files

# textual_matches <stem> <dir> <include-glob>  -> matching files, one per line.
# Always exits 0: with `set -e` + `pipefail`, a grep that finds nothing would
# otherwise abort the caller through its command substitution.
textual_matches() {
  { grep -rl --include="$3" -F "$1" "$2" 2>/dev/null || true; } | sort -u || true
}

mode_changed() {
  pick_python; set_pytest_cmd
  local base
  # CHANGED_BASE lets you diff against something narrower (e.g. HEAD for
  # uncommitted work) instead of the whole branch.
  base="${CHANGED_BASE:-}"
  if [[ -z "$base" ]]; then
    base=$(git -C "$REPO_ROOT" merge-base HEAD origin/main 2>/dev/null \
        || git -C "$REPO_ROOT" merge-base HEAD main 2>/dev/null \
        || git -C "$REPO_ROOT" rev-parse HEAD~1 2>/dev/null || true)
  fi
  [[ -n "$base" ]] || die "cannot determine a base revision to diff against"

  say "${C_BOLD}changed${C_OFF} ${C_DIM}— heuristic mapping from the diff vs $(git -C "$REPO_ROOT" rev-parse --short "$base")${C_OFF}"

  mapfile -t changed < <(
    git -C "$REPO_ROOT" diff --name-only "$base" -- . 2>/dev/null
    git -C "$REPO_ROOT" ls-files --others --exclude-standard 2>/dev/null
  )

  local backend_targets=() vitest_targets=() e2e_targets=() unmapped=()
  local f stem cand n shared_config=0

  for f in "${changed[@]}"; do
    [[ -n "$f" ]] || continue
    case "$f" in
      backend/tests/conftest.py|backend/pytest.ini|backend/pyproject.toml)
        # These change behaviour for every test, so guessing is wrong: run the
        # same set `fast` runs.
        shared_config=1 ;;
      backend/tests/test_*.py)
        backend_targets+=("${f#backend/}") ;;                    # 1. the test itself
      backend/*.py)
        stem="$(basename "$f" .py)"
        if [[ -f "$BACKEND/tests/test_$stem.py" ]]; then         # 2. exact match
          backend_targets+=("tests/test_$stem.py")
          continue
        fi
        # 3. textual, capped
        n=$(textual_matches "$stem" "$BACKEND/tests" 'test_*.py' | wc -l)
        if (( n > 0 && n <= TEXT_MATCH_CAP )); then
          while IFS= read -r cand; do
            backend_targets+=("tests/$(basename "$cand")")
          done < <(textual_matches "$stem" "$BACKEND/tests" 'test_*.py')
        else
          unmapped+=("$f")
        fi
        ;;
      frontend/tests/*.spec.ts)
        e2e_targets+=("${f#frontend/}") ;;                       # 1. the spec itself
      frontend/src/*|frontend/tests/*)
        stem="$(basename "$f" | sed -E 's/\.(ts|js|svelte)$//')"
        n=$(textual_matches "$stem" "$FRONTEND/src/tests" '*.test.ts' | wc -l)
        if (( n > 0 && n <= TEXT_MATCH_CAP )); then
          while IFS= read -r cand; do
            vitest_targets+=("${cand#"$FRONTEND"/}")
          done < <(textual_matches "$stem" "$FRONTEND/src/tests" '*.test.ts')
        fi
        while IFS= read -r cand; do
          if [[ -n "$cand" ]]; then e2e_targets+=("${cand#"$FRONTEND"/}"); fi
        done < <(ls "$FRONTEND"/tests/*"$stem"*.spec.ts 2>/dev/null | sort -u)
        ;;
    esac
  done

  # de-duplicate
  local -a bt vt et
  if (( shared_config )); then
    note "conftest.py / pytest.ini changed — they affect every test, so the fast set is used"
    mapfile -t bt < <(fast_test_files)
  else
    mapfile -t bt < <(printf '%s\n' "${backend_targets[@]:-}" | grep -v '^$' | sort -u || true)
  fi
  mapfile -t vt < <(printf '%s\n' "${vitest_targets[@]:-}"  | grep -v '^$' | sort -u || true)
  mapfile -t et < <(printf '%s\n' "${e2e_targets[@]:-}"     | grep -v '^$' | sort -u || true)

  # A mapped file may itself be one of the slow ones (you changed pdf_engine, so
  # test_pdf_engine is the right test to run) — say so rather than let the user
  # wonder why `changed` is slower than `fast`.
  local s slow_hit=()
  for s in "${bt[@]:-}"; do
    local q
    for q in "${SLOW_TESTS[@]}"; do
      if [[ "$s" == "$q" ]]; then slow_hit+=("$s"); fi
    done
  done
  if (( ${#slow_hit[@]} )); then
    note "note: ${slow_hit[*]} is in SLOW_TESTS — expect minutes, not seconds"
  fi

  note "changed files: ${#changed[@]}   →  backend ${#bt[@]}, vitest ${#vt[@]}, e2e ${#et[@]}"
  if (( ${#unmapped[@]} )); then
    note "no test mapped for ${#unmapped[@]} changed module(s) (nothing named after them,"
    note "or referenced too widely to guess): ${unmapped[*]:0:180}"
  fi
  if (( ${#bt[@]} >= BIG_DIFF_WARN )); then
    note "that is a large slice of the suite — './scripts/test.sh fast' is the cheaper, honest check"
  fi

  if (( ${#bt[@]} == 0 && ${#vt[@]} == 0 && ${#et[@]} == 0 )); then
    note "no tests mapped from this diff — this is a heuristic, so run './scripts/test.sh fast'"
    return 0
  fi

  if (( ${#bt[@]} )); then
    note "backend: ${bt[*]}"
    run_suite "backend changed (${#bt[@]} files)" "$T_BACKEND" "$BACKEND" "${PYTEST[@]}" "${bt[@]}"
  fi
  if (( ${#vt[@]} )); then
    note "vitest:  ${vt[*]}"
    run_suite "vitest changed (${#vt[@]} files)" "$T_UNIT" "$FRONTEND" npm run test:unit -- "${vt[@]}"
  fi
  if (( ${#et[@]} )); then
    note "e2e:     ${et[*]}"
    run_suite "playwright changed (${#et[@]} specs)" "$T_E2E" "$FRONTEND" npm run test:e2e -- "${et[@]}"
  fi
}

# ---------------------------------------------------------------------------
# full / gates
# ---------------------------------------------------------------------------
mode_full() {
  pick_python; set_pytest_cmd
  say "${C_BOLD}full${C_OFF} ${C_DIM}— backend + unit + e2e, each with its own timeout${C_OFF}"
  run_suite "backend all" "$T_FULL_BACKEND" "$BACKEND" "${PYTEST[@]}" tests/
  run_suite "vitest (all)" "$T_UNIT" "$FRONTEND" npm run test:unit
  run_suite "playwright (all)" "$T_E2E" "$FRONTEND" npm run test:e2e
}

mode_gates() {
  pick_python; set_pytest_cmd
  say "${C_BOLD}gates${C_OFF} ${C_DIM}— the four handoff gates (unit, e2e, check, pytest)${C_OFF}"
  run_suite "vitest (test:unit)" "$T_UNIT" "$FRONTEND" npm run test:unit
  run_suite "playwright (test:e2e)" "$T_E2E" "$FRONTEND" npm run test:e2e
  run_suite "svelte-check (check)" "$T_CHECK" "$FRONTEND" npm run check
  run_suite "pytest (backend all)" "$T_FULL_BACKEND" "$BACKEND" "${PYTEST[@]}" tests/
}

usage() {
  cat <<EOF
${C_BOLD}scripts/test.sh${C_OFF} — run only the tests you need.

${C_BOLD}USAGE${C_OFF}
  ./scripts/test.sh [mode] [args...]

${C_BOLD}MODES${C_OFF}
  ${C_BOLD}fast${C_OFF}                     (default) curated backend subset, ~40 s.
                           Excludes only the files in SLOW_TESTS.
  ${C_BOLD}slow${C_OFF}                     only the files \`fast\` excludes (~1.5 min).
  ${C_BOLD}backend${C_OFF} [target...]      one backend file, node id, or -k pattern.
                             ./scripts/test.sh backend test_tts_engine
                             ./scripts/test.sh backend tests/test_folders.py
                             ./scripts/test.sh backend -k "cache and speed"
                           No target = the whole backend suite.
  ${C_BOLD}unit${C_OFF} [pattern...]        frontend vitest (all, or matching files).
  ${C_BOLD}e2e${C_OFF} [spec...]            Playwright, @critical flows only (32 tests).
                             ./scripts/test.sh e2e library
  ${C_BOLD}check${C_OFF}                    svelte-check only.
  ${C_BOLD}changed${C_OFF}                  tests mapped from the diff vs merge-base(HEAD, origin/main).
                           A HEURISTIC — it can miss indirect coverage.
  ${C_BOLD}full${C_OFF}                     backend + unit + e2e.
  ${C_BOLD}gates${C_OFF}                    the four handoff gates: test:unit, test:e2e,
                           npm run check, pytest.

${C_BOLD}OPTIONS${C_OFF}
  -v, --verbose            stream full output instead of a summary.
  -h, --help               this help.
  --list                   print the files each of fast/slow selects, then exit.

${C_BOLD}TIMEOUTS (seconds, override via environment)${C_OFF}
  T_FAST=$T_FAST  T_BACKEND=$T_BACKEND  T_SLOW=$T_SLOW
  T_FULL_BACKEND=$T_FULL_BACKEND  T_UNIT=$T_UNIT  T_E2E=$T_E2E  T_CHECK=$T_CHECK

  Every suite runs under \`timeout\`. A killed suite is reported as TIMEOUT
  (exit != 0), never as a pass or a test failure.

${C_BOLD}ENVIRONMENT${C_OFF}
  TEST_PY                  python to use. Default: backend/.venv/bin/python,
                           else backend/venv/bin/python, else \`uv run python\`.

${C_BOLD}NOTES${C_OFF}
  * e2e needs the dev server on :5173 (playwright starts it) and, for the spec's
    own assertions, the API on :8000 (cd backend && uv run uvicorn main:app).
  * The backend suite is run with CWD=backend; a tmp working dir is created by
    tests/conftest.py so tests cannot write into the repository.
  * pytest's cacheprovider is disabled (see backend/pytest.ini), so --lf/--ff are
    unavailable by design.
EOF
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
ARGS=()
while (( $# )); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -v|--verbose) VERBOSE=1; shift ;;
    --list)
      pick_python; set_pytest_cmd
      say "fast (${#SLOW_TESTS[@]} excluded):"
      fast_test_files | sed 's/^/  /'
      say "slow:"
      slow_test_files | sed 's/^/  /'
      exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

MODE="${ARGS[0]:-fast}"
if (( ${#ARGS[@]} )); then ARGS=("${ARGS[@]:1}"); else ARGS=(); fi

case "$MODE" in
  fast)    mode_fast ;;
  slow)    mode_slow ;;
  backend) mode_backend ${ARGS[@]+"${ARGS[@]}"} ;;
  unit)    mode_unit ${ARGS[@]+"${ARGS[@]}"} ;;
  e2e)     mode_e2e ${ARGS[@]+"${ARGS[@]}"} ;;
  check)   mode_check ;;
  changed) mode_changed ;;
  full)    mode_full ;;
  gates)   mode_gates ;;
  *)       die "unknown mode '$MODE' (try --help)" ;;
esac

summary
