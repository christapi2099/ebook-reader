# AMD GPU (ROCm) backend environment

Use this when you have an AMD GPU and want Kokoro to synthesise on it instead of the
CPU. NVIDIA users do nothing: `backend/.venv` already has CUDA torch.

## Set up (one command)

```bash
./scripts/setup-rocm-env.sh          # creates backend/.venv-rocm; idempotent
```

It installs the pinned dependencies from `backend/requirements.txt` (an export of
`uv.lock`) without the CUDA wheels (`torch`, `torchvision`, `triton`, `nvidia-*`,
`cuda-*`), then installs ROCm torch/torchvision from
`https://download.pytorch.org/whl/rocm7.1`. It prints torch/HIP/device info at the end
and refuses to target `backend/.venv`. Override the location with `ROCM_VENV=...`.

## Run

```bash
GPU_ENV=rocm ./start.sh              # backend via backend/.venv-rocm, never `uv run`
```

Tests in the ROCm env (the default `./scripts/test.sh` still uses `backend/.venv`):

```bash
TEST_PY=backend/.venv-rocm/bin/python ./scripts/test.sh fast
```

Cards that ROCm does not officially support need an override, e.g. RX 6700 XT
(gfx1031) uses the gfx1030 kernels: `export HSA_OVERRIDE_GFX_VERSION=10.3.0` (or put it in
`backend/.env`; see `backend/.env.example`). `HIP_VISIBLE_DEVICES` selects a GPU. They
must be set before torch initialises.

## Why a separate environment

`uv.lock` pins CUDA torch and `nvidia-*` wheels, so one lock cannot also describe ROCm
torch. Verified uv behaviour that makes sharing dangerous:

- `uv pip install` ignores `UV_PROJECT_ENVIRONMENT` and writes into whichever env it
  finds, so it can overwrite the main env's torch.
- `uv run` / `uv sync` against an alternate env re-sync that env to the lock, silently
  replacing ROCm torch with CUDA torch.
- `uv run --no-sync` and `UV_NO_SYNC=1` are safe.

So the ROCm env is only ever driven by `scripts/setup-rocm-env.sh` and its own
`bin/python`. Never run `uv sync`/`uv run` against it. Do not add PyPI as an
`--extra-index-url` when installing torch: uv ranks extra indexes above `--index-url`,
and PyPI's CUDA torch would win.

Docker stays CUDA-only; there is no ROCm image.

## Parallel render workers (GPU)

A book render on the GPU engine (torch Kokoro on CUDA/ROCm) runs **4 synthesis worker
processes** by default, each with its own Kokoro model (~1.8 GB VRAM each, spawned when a
render starts and released when it ends). Measured on an RX 6700 XT (12 GB): 1 process 24x
real time, 4 processes 45x; 6 is slower and 8 runs out of memory. Threads inside one process
were tried and lose to the GIL (about 29x), hence processes. The CTC aligner stays shared in the
main process.

- `RENDER_SYNTH_WORKERS=N` (environment, read at render start) always wins; `1` restores the
  old serial render. It is an environment variable, not a persisted app setting (the repo has no
  settings mechanism for render knobs; `RENDER_STALL_TIMEOUT_S` is the same). Other engines
  still default to 1.
- VRAM safety: at render start the worker count is capped at
  `floor((free VRAM - 1 GiB) / per-worker footprint)` and the reduction is logged
  (`RENDER_GPU_WORKER_VRAM_GB`, default 1.8, overrides the footprint). If a worker still hits an
  out-of-memory error, that worker exits, the count drops by one (down to 1) and the sentence is
  retried; nothing fails the render.
- Output is identical to a 1-worker render: sentences are consumed in order and the manifest is
  unchanged.

## Troubleshooting

- `torch.cuda.is_available()` is False: check `groups` includes `render` and `video`
  (re-login after `usermod -aG`), `/dev/kfd` is readable, and `rocminfo` lists your GPU.
- "HIP error: invalid device function" / "no kernel image": the card has no kernels in
  this ROCm build. Set `HSA_OVERRIDE_GFX_VERSION` to the nearest supported arch
  (10.3.0 for RDNA2, 11.0.0 for RDNA3).
- `miopenStatusUnknownError` / "fatal error: 'rocrand/rocrand_xorwow.h' file not found"
  during Kokoro synthesis: MIOpen JIT-compiles a dropout kernel for Kokoro's LSTMs and
  needs the rocrand development headers, which the torch wheel does not include. Install
  them from the system ROCm packages (Fedora: `sudo dnf install rocrand-devel`, matching
  the runtime version). The setup script's verification prints `LSTM+dropout: FAILED`
  when they are missing. Stop-gap without root: point `CPLUS_INCLUDE_PATH` and
  `C_INCLUDE_PATH` at any directory containing a `rocrand/` header tree.
- First GPU run is slow for a while: MIOpen compiles convolution/LSTM kernels and caches
  them under `~/.cache/miopen` and `~/.config/miopen`; later runs are fast.
- `torch.version.cuda` must be `None` and `torch.version.hip` set in this env. If
  `nvidia-*` wheels appear, delete `backend/.venv-rocm` and re-run the setup script.
