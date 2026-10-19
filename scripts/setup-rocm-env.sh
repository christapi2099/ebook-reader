#!/usr/bin/env bash
#
# Create / refresh the AMD ROCm backend environment: backend/.venv-rocm
#
#   ./scripts/setup-rocm-env.sh            # idempotent; safe to re-run
#   ROCM_VENV=/path/to/venv ./scripts/setup-rocm-env.sh
#   ROCM_INDEX=https://download.pytorch.org/whl/rocm7.1 ...   # override the wheel index
#
# Why a separate env: backend/uv.lock pins CUDA torch + nvidia-* wheels, so one lock
# cannot also hold ROCm torch. This env is installed from the SAME requirements.txt
# (an export of that lock) minus the CUDA-only wheels, then torch/torchvision come
# from the ROCm index. See docs/AMD_ROCM.md.
#
# SAFETY: never run `uv sync` / `uv run` against this env, and never `uv pip install`
# without `--python`. `uv pip install` ignores UV_PROJECT_ENVIRONMENT and would
# overwrite whichever env it finds, i.e. possibly backend/.venv. Every install below
# passes an explicit --python, and the script refuses to target backend/.venv.
set -euo pipefail

die() { echo "setup-rocm-env: ERROR: $*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
ROCM_VENV="${ROCM_VENV:-$BACKEND/.venv-rocm}"
ROCM_INDEX="${ROCM_INDEX:-https://download.pytorch.org/whl/rocm7.1}"
REQS="$BACKEND/requirements.txt"

command -v uv >/dev/null 2>&1 || die "uv not found on PATH (https://docs.astral.sh/uv/)"
[[ -f "$REQS" ]] || die "missing $REQS"

# Refuse to touch the main (CUDA) env, however the path is spelled (symlinks included).
mkdir -p "$(dirname "$ROCM_VENV")"
target_real="$(realpath -m "$ROCM_VENV")"
for main_env in "$BACKEND/.venv" "$BACKEND/venv"; do
  if [[ -e "$main_env" ]] && [[ "$target_real" == "$(realpath -m "$main_env")" ]]; then
    die "ROCM_VENV resolves to $main_env, the main CUDA environment. Refusing."
  fi
done
case "$(basename "$target_real")" in
  .venv|venv) die "ROCM_VENV ($target_real) is named like the main env. Use something like backend/.venv-rocm." ;;
esac

if [[ ! -x "$ROCM_VENV/bin/python" ]]; then
  echo "==> creating $ROCM_VENV (python 3.12)"
  uv venv "$ROCM_VENV" --python 3.12
else
  echo "==> reusing $ROCM_VENV"
fi
PY="$ROCM_VENV/bin/python"

# 1. Everything except torch. requirements.txt is `uv export` of the lock and pins
#    CUDA wheels. Filter them out with an explicit pattern: torch, torchvision, triton
#    (CUDA triton; ROCm torch pulls its own triton from the ROCm index), nvidia-*,
#    and cuda-* (cuda-bindings, cuda-pathfinder, cuda-toolkit). The "# via" comment
#    lines are kept; they are inert.
FILTERED_REQS="$(mktemp)"
trap 'rm -f "$FILTERED_REQS"' EXIT
grep -Ev '^(nvidia-|cuda-|torch==|torchvision==|triton==)' "$REQS" > "$FILTERED_REQS"
echo "==> installing backend dependencies ($(grep -cE '^[A-Za-z]' "$FILTERED_REQS") requirements; CUDA wheels filtered out)"
# --no-deps: the export is already the complete, pinned closure. Without it uv would
#    resolve kokoro's `torch` requirement from PyPI and pull CUDA torch + nvidia-*.
uv pip install --python "$PY" --no-deps -r "$FILTERED_REQS"

# 2. torch/torchvision from the ROCm index ONLY (it also serves triton and torch's
#    pure-Python deps: sympy, networkx, ...). Do NOT add PyPI as --extra-index-url:
#    uv ranks extra indexes ABOVE --index-url, so PyPI's CUDA torch would win.
#    No --index-strategy flag is needed because there is only one index.
echo "==> installing torch/torchvision from $ROCM_INDEX"
uv pip install --python "$PY" --reinstall-package torch --reinstall-package torchvision \
  torch torchvision --index-url "$ROCM_INDEX"

# 3. Guard: no CUDA wheels may exist in this env.
leftover="$(uv pip list --python "$PY" 2>/dev/null | awk 'tolower($1) ~ /^(nvidia-|cuda-)/ {print $1}' || true)"
if [[ -n "$leftover" ]]; then
  echo "setup-rocm-env: WARNING: CUDA wheels present in the ROCm env:" >&2
  echo "$leftover" >&2
  die "remove them (delete $ROCM_VENV and re-run)"
fi

echo
echo "==> verification"
"$PY" - <<'PYEOF'
import torch
print("torch            :", torch.__version__)
print("torch.version.hip:", torch.version.hip)
print("torch.version.cuda:", torch.version.cuda)
ok = torch.cuda.is_available()
print("cuda.is_available:", ok)
if ok:
    print("device           :", torch.cuda.get_device_name(0), torch.cuda.get_device_properties(0).gcnArchName)
else:
    print("device           : none (see docs/AMD_ROCM.md, Troubleshooting)")
if ok:
    # Kokoro's LSTMs have dropout; MIOpen JIT-compiles a dropout kernel that needs the
    # rocrand HEADERS, which the torch wheel does not ship. Fail here, not mid-synthesis.
    try:
        torch.nn.LSTM(8, 8, num_layers=2, dropout=0.2).cuda()(torch.randn(3, 1, 8).cuda())
        print("LSTM+dropout     : ok")
    except Exception as e:
        print("LSTM+dropout     : FAILED:", str(e)[:120])
        print("  -> install the rocrand headers (Fedora: sudo dnf install rocrand-devel)")
        print("     see docs/AMD_ROCM.md, Troubleshooting")
if torch.version.hip is None or torch.version.cuda is not None:
    raise SystemExit("NOT a ROCm build of torch")
PYEOF
