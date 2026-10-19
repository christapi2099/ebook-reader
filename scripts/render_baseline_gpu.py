"""Run backend/scripts/render_baseline.py with Kokoro on the GPU (ROCm or CUDA).

The harness hard-wires ``engine_manager.build_local("cpu")`` for ``kokoro-cpu``. This
wrapper patches that one call to the GPU device and then delegates to the harness
unchanged, so the same real pipeline and the same statistical gates apply. Nothing
under backend/services is modified.

    cd backend && .venv-rocm/bin/python ../scripts/render_baseline_gpu.py \
        compare --engine kokoro-cpu --golden .baseline/kokoro-cpu.json [--keep DIR]

(Never use ``capture`` with this wrapper: it would overwrite the CPU golden.)
"""
import runpy
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

if "capture" in sys.argv[1:]:
    raise SystemExit("refusing 'capture': it would overwrite the CPU golden with GPU output")

from services import engine_manager  # noqa: E402

_orig = engine_manager.build_local


def _gpu_build_local(device):
    print(f"[render_baseline_gpu] build_local({device!r}) -> 'cuda'", file=sys.stderr)  # "cuda" == HIP on ROCm
    return _orig("cuda")


engine_manager.build_local = _gpu_build_local

sys.argv[0] = str(BACKEND / "scripts" / "render_baseline.py")
runpy.run_path(sys.argv[0], run_name="__main__")
