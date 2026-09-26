# Backend migration to `uv`

Status: **complete and verified** in this checkout. `uv` now owns the backend
environment (`backend/.venv`) and the lockfile (`backend/uv.lock`).
The legacy hand-made `backend/venv` was left **completely untouched** and still works.

- Tool: `uv 0.11.3 (x86_64-unknown-linux-gnu)` at `/home/christia50/.local/bin/uv`
  (also on `PATH` as `uv` in this shell).
- Interpreter used for the new environment: **CPython 3.12.3** (`/usr/bin/python3`),
  i.e. exactly the interpreter the legacy venv was built from.
- Result: `uv sync` reproduces the legacy venv's package set **version-for-version**
  (166 distributions compared; the only differences are that the uv env has no `pip`
  distribution, and `en_core_web_sm` is now a declared dependency — see §7).

---

## 1. Python version discrepancy (flagged, as requested)

| Source | Claimed | Reality |
|---|---|---|
| `CLAUDE.md` (before this change) | "Backend (Python 3.11, port 8000)" | wrong |
| `CLAUDE.md` hard rule | `source backend/venv/bin/activate` | venv is 3.12.3 |
| `backend/venv/pyvenv.cfg` | — | `3.12.3` |
| `backend/Dockerfile` | — | `python3.12` (both stages) |
| `implementation-handoff.md` §9.3 | "only **3.12.3** present; `CLAUDE.md` documents 3.11" | confirms the drift |
| `/usr/bin/python3` | — | `3.12.3` |

`CLAUDE.md` was stale; the migration sets `requires-python = ">=3.12,<3.13"` in
`backend/pyproject.toml` (matching the real interpreter, the Dockerfile and the lock) and
corrected the `CLAUDE.md` "Run" block to say Python 3.12. **No Python 3.11 exists on this
host**, so nothing was built against 3.11.

---

## 2. Commands run, verbatim

```bash
# --- fact finding ---
uv --version                                    # uv 0.11.3 (x86_64-unknown-linux-gnu)
backend/venv/bin/python --version               # Python 3.12.3
backend/venv/bin/python -m pip freeze           # 166 distributions
backend/venv/bin/python -c "import torch; ..."  # torch 2.14.0+cu130, cuda 13.0

# --- environment for uv in THIS sandbox only (see §10) ---
export UV_CACHE_DIR=/home/christia50/Repos/ebook-reader/.uv-cache
export UV_PYTHON_PREFERENCE=only-system

# --- lock ---
cd backend && uv lock                           # 1st attempt: WRONG torch (see §6)
cd backend && uv lock --upgrade --no-cache      # after index-strategy fix
                                                # -> "Resolved 150 packages in 8.17s"

# --- create env + install ---
cd backend && uv sync                           # created backend/.venv, installed 150 pkgs

# --- verification ---
cd backend && uv run python -c "import torch, kokoro, fastapi, sqlmodel, fitz, spacy; print(torch.__version__, torch.version.cuda)"
cd backend && uv run python -c "import main; print('app import OK:', type(main.app))"
cd backend && uv run pytest tests/test_db_models.py -q
cd /home/christia50/Repos/ebook-reader && uv run --project backend pytest backend/tests/test_db_models.py -q
cd backend && uv lock --check                   # -> "lock in sync with pyproject"
bash -n start.sh                                # shell syntax check

# --- regenerate the pip-compatibility requirements file ---
cd backend && uv export --format requirements-txt --no-hashes > requirements.txt
```

`uv venv` was **not** needed: `uv sync` creates `backend/.venv` on demand.
`backend/venv` was never passed to any uv command and was never written to.

---

## 3. Verification output

All four checks in the brief passed.

**a) Key imports + torch/CUDA**

```
$ cd backend && uv run python -c "import torch, kokoro, fastapi, sqlmodel, fitz, spacy; print(torch.__version__, torch.version.cuda)"
.../site-packages/torch/cuda/__init__.py:1174: UserWarning: Can't initialize NVML
  raw_cnt = _raw_device_count_nvml()
warning: The `fitz` API is deprecated and will be removed in future. Use `import pymupdf` instead.
2.14.0+cu130 13.0
```

**b) Application import**

```
$ cd backend && uv run python -c "import main; print('app import OK:', type(main.app))"
app import OK: <class 'fastapi.applications.FastAPI'>
```

**c) Smoke test (one small file, as instructed — the full suite was not run)**

```
$ cd backend && uv run pytest tests/test_db_models.py -q
......                                                                   [100%]
6 passed in 0.86s
```

Also verified from the repo root, since `uv` needs a project:

```
$ uv run --project backend pytest backend/tests/test_db_models.py -q
......                                                                   [100%]
6 passed in 1.65s
```

**d) Environment equality with the legacy venv**

```
counts: old=166 new=167
== only in legacy venv ==        (nothing)
== only in uv .venv ==
pip 26.2.1
== version mismatches ==
en_core_web_sm: @ -> 3.8.0       (pip freeze prints the URL, not the version — not a real diff)
```

Every shared package is the same version, including
`torch 2.14.0+cu130`, `torchvision 0.29.0`, `spacy 3.8.16`, `numpy 2.5.3`,
`fastapi 0.141.1`, `sqlmodel 0.0.47`, `kokoro 0.9.4`.

**e) Legacy venv integrity**

```
$ ls -l backend/venv/bin/python  -> present
$ backend/venv/bin/python -c "import torch,sys; print(sys.version.split()[0], torch.__version__)"
3.12.3 2.14.0+cu130
$ stat -c '%y %n' backend/venv backend/venv/pyvenv.cfg
2026-09-25 21:19:42 venv          # mtimes predate this session: untouched
2026-09-25 21:12:03 venv/pyvenv.cfg
```

---

## 4. Files added / changed

| File | Change |
|---|---|
| `backend/pyproject.toml` | **Extended.** Added `[project]` (name, `requires-python`, runtime deps), `[dependency-groups] dev`, `[tool.uv]` (`package = false`, `index-strategy`), `[[tool.uv.index]] pytorch-cu124`. The pre-existing `[tool.pytest.ini_options]` and `[tool.coverage.run]` blocks were kept **byte-for-byte** (another change owns test config; `backend/pytest.ini` is its replacement). |
| `backend/uv.lock` | **New, committed** (not ignored — checked with `git check-ignore`). 150 packages, `requires-python = "==3.12.*"`. |
| `backend/requirements.txt` | **Now generated** from the lock (`uv export`), exact pins, with a "DO NOT EDIT BY HAND" header. Kept because `backend/Dockerfile` consumes it. |
| `start.sh` | Backend start now prefers `uv run` (which syncs `.venv` from `uv.lock`), with fallbacks to `backend/.venv` and finally the legacy `backend/venv`, so it still works for someone without uv. |
| `CLAUDE.md` | "Run" block → `cd backend && uv sync && uv run uvicorn main:app --reload`, Python 3.11 → 3.12. Hard rule `source backend/venv/bin/activate` → `cd backend && uv run <cmd>` + `uv add` guidance. Nothing else touched. |
| `.opencode/instructions.md` | `## Backend venv` section → `## Backend environment (uv)`; activation line replaced with `uv run`; legacy activation kept as a documented fallback. |
| `docker-compose.dev.yml` | `./backend/venv:/app/venv` → `./backend/.venv:/app/.venv` (mount was unused by the image, which installs its own deps). |
| `backend/Dockerfile` | Comment only, above `COPY requirements.txt .`, recording that the file is generated from `uv.lock`. Build logic unchanged. |
| `.gitignore` | Added `.uv-cache/` (local uv cache, sandbox workaround) with an explicit note that **`uv.lock` is intentionally not ignored**. |
| `backend/.venv/` | **New environment** created by `uv sync` (already covered by the existing `.venv/` ignore rule). |
| `backend/UV_MIGRATION.md` | This report. |

No source file under `backend/services/`, `backend/routers/`, `backend/db/` or
`backend/tests/` was touched.

---

## 5. Dependency groups (runtime vs dev)

The old `backend/requirements.txt` **mixed test tooling into the runtime set**. Split:

- runtime → `[project.dependencies]`: `fastapi>=0.115`, `uvicorn[standard]`, `sqlmodel`,
  `pymupdf`, `ebooklib`, `beautifulsoup4`, `spacy`, `easyocr`, `python-multipart`,
  `soundfile`, `numpy`, `torch>=2.4.0`, `torchvision`, `kokoro>=0.9.2`,
  `en_core_web_sm @ <wheel url>`.
- dev/test → `[dependency-groups] dev`: `pytest`, `pytest-asyncio`, `pytest-cov`, `httpx`.

`httpx` was checked before moving: no non-test module imports it
(`grep -rn httpx main.py services routers db` → no hits); it is required only by
`starlette.testclient.TestClient`, which `backend/tests/*` use. `pytest*` are test-only by
nature.

`uv sync` installs the dev group by default, so the default `backend/.venv` matches the old
venv exactly; use `uv sync --no-dev` for a production-style env.

---

## 6. torch / CUDA handling (and the one real trap)

**Requirement kept as-is.** `torch>=2.4.0` (CUDA-capable) and `torchvision` are unchanged
from the old `requirements.txt`. No CPU wheel was substituted and nothing was pinned to an
older CUDA build.

**The index is kept, as a supplemental index:**

```toml
[[tool.uv.index]]
name = "pytorch-cu124"
url = "https://download.pytorch.org/whl/cu124"
```

which is the direct translation of the old
`--extra-index-url https://download.pytorch.org/whl/cu124`.

**The trap.** With uv's default index strategy (`first-index`, i.e. "take the first index
that has the package"), the first lock **downgraded torch to `2.6.0+cu124`** and pulled
obsolete transitive packages off the pytorch index — `certifi 2022.12.7`, `idna 3.4`,
`charset-normalizer 2.1.1`, `numpy 2.5.2`. That violates "do not change dependency
versions" and is also the classic `--extra-index-url` dependency-confusion hazard.
The pytorch cu124 index tops out at `torch 2.6.0` (verified by listing the index), while
PyPI carries `torch 2.14.0` — the CUDA build the working venv already had.

**Fix**, in `backend/pyproject.toml`:

```toml
[tool.uv]
index-strategy = "unsafe-best-match"
```

This is uv's documented equivalent of pip's `--extra-index-url` behaviour: consider every
index and take the best match. After that (`uv lock --upgrade --no-cache`) the lock resolves
`torch 2.14.0` and `torchvision 0.29.0` **from PyPI** — the CUDA-enabled artifacts
(`torch.__version__ == "2.14.0+cu130"`, `torch.version.cuda == "13.0"`), identical to the
legacy venv.

`[tool.uv.sources]` was deliberately **not** used to pin `torch`/`torchvision` to the
`pytorch-cu124` index: that would have forced the older `2.6.0+cu124` build and changed a
dependency version.

**GPU status in this sandbox.** `torch.cuda.is_available()` is `False` because `/dev/nvidia*`
is not exposed to the sandbox (`nvidia-smi` cannot talk to the driver). This is an
environment restriction, not a packaging problem — `torch.version.cuda == "13.0"` proves the
installed wheel is the CUDA build, and the same interpreter outside the sandbox sees the
T600 (cc 7.5). Nothing was "fixed" because of it.

---

## 7. `en_core_web_sm` — a gap the old `requirements.txt` had

`backend/services/base_engine.py` calls `spacy.load("en_core_web_sm")` (with a
`python -m spacy download` fallback). The model **was installed in `backend/venv`**
(`en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/...whl`)
but was **not** listed in `requirements.txt`; it only got installed via the Dockerfile's
separate `python3 -m spacy download en_core_web_sm` step. Left as-is, `uv sync` would have
produced an environment where sentence splitting silently falls back to a runtime download.

It is therefore declared as a direct dependency in `[project.dependencies]`, at the exact
version already present in the venv (3.8.0, which requires `spacy>=3.8.0,<3.9.0`; the lock
resolves `spacy 3.8.16`, the same as the venv). The Dockerfile's `spacy download` line was
left in place — it is now redundant but harmless.

---

## 8. `requirements.txt` decision: **keep it, as a generated artifact**

Reasoning:

- `backend/Dockerfile` still consumes it (`COPY requirements.txt .` + `pip install -r`), so
  deleting it would have required rewriting and re-verifying the container build. `docker`
  is on `PATH` here but the daemon/CUDA base image build was not exercised, so that change
  could not have been verified.
- It is now **derived** from `backend/uv.lock` via
  `uv export --format requirements-txt --no-hashes`, so it cannot drift, and every pin is an
  exact `==`, including `torch==2.14.0` (the CUDA build).
- The regenerated file contains all groups (runtime **and** dev), matching what the old
  file contained, so the pip/Docker path is not silently slimmed down.
- The `--extra-index-url` line was intentionally **not** copied into the generated file:
  uv's export does not emit index directives, and every pinned version was checked to exist
  on PyPI (`triton 3.8.0`, `nvidia-cuda-runtime 13.0.96`, `nvidia-cudnn-cu13 9.24.0.43` all
  present), so pip installs the same artifacts from the default index. The pytorch index
  lives in `pyproject.toml`, where uv uses it.
- If a future pin is ever only published on the pytorch index, re-add
  `--extra-index-url https://download.pytorch.org/whl/cu124` to `requirements.txt`.

A follow-up worth doing later (not done here, unverifiable without a Docker build):
switch the Dockerfile to `uv sync --frozen --no-dev` and drop `requirements.txt`.

---

## 9. Every `venv` / `pip install` / `requirements.txt` reference found, and its disposition

| Reference | Disposition |
|---|---|
| `CLAUDE.md:8` — `source venv/bin/activate && uvicorn main:app --reload` | **Updated** → `uv sync && uv run uvicorn main:app --reload` |
| `CLAUDE.md:30` — hard rule `source backend/venv/bin/activate` | **Updated** → `cd backend && uv run <cmd>` (env `backend/.venv`, lock `backend/uv.lock`) |
| `start.sh:35-44` — hard-required `backend/venv`, printed `python3 -m venv venv && pip install -r requirements.txt` | **Rewritten**: `uv run` → `backend/.venv` → legacy `backend/venv`, with a uv install hint in the error path |
| `backend/Dockerfile:12` — `COPY requirements.txt .` | **Kept** (file still exists), explanatory comment added |
| `backend/Dockerfile:13` — `pip install -r requirements.txt` | **Kept** unchanged; documented as the legacy pip path |
| `backend/Dockerfile:14` — `python3 -m spacy download en_core_web_sm` | **Kept**; now redundant (model is in `requirements.txt`/lock) |
| `backend/requirements.txt` | **Kept but regenerated** as a generated export of `uv.lock` (§8) |
| `docker-compose.dev.yml:13` — `./backend/venv:/app/venv` | **Updated** → `./backend/.venv:/app/.venv` (the mount was unused by the image) |
| `.opencode/instructions.md:44` — `source /home/christopia/.../backend/venv/bin/activate` (path had a typo and did not resolve) | **Replaced** with a `## Backend environment (uv)` section; legacy activation kept as a fallback line |
| `backend/.dockerignore:4-5` — `.venv/`, `venv/` | **No change needed** — already excludes both environments |
| `scripts/build-deb.sh:14` — `--exclude='venv/' --exclude='.venv/'` | **No change needed** — already excludes both |
| `.memory/bugs.md:39`, `.memory/architecture.md:52` — `.venv/bin/uvicorn ...` notes | **Left as-is** (historical notes; `.memory/` is gitignored) |
| `.claude/settings.local.json:11-12` — allowlist entries for `backend/venv/bin/python` | **Left as-is** (local tool allowlist; harmless — `backend/venv` still exists). A human may want to add `uv run` allowlist entries. |
| `implementation-handoff.md:262,274` — "start.sh hard-requires backend/venv, which does not exist", "CLAUDE.md documents 3.11" | **Left as-is** (historical handoff document; its findings are now addressed by this migration, and it is untracked/not part of the build) |
| `verify-config.sh`, `docker-compose.yml`, `DOCKER_SETUP.md`, `frontend/**`, `flatpak/**` | **No references to the Python env found** — nothing to do |

No CI configuration exists in the repo (`.github/` is absent), so nothing else consumed
`requirements.txt`.

---

## 10. Sandbox note: `UV_CACHE_DIR`

Inside this agent sandbox `~/.cache/uv` is mounted **read-only**, so plain `uv run` fails with:

```
error: Could not acquire lock
  Caused by: Could not create temporary file
  Caused by: Read-only file system (os error 30) at path "/home/christia50/.cache/uv/.tmpIODPoD"
```

Workaround used here (a local cache directory inside the repo, gitignored):

```bash
export UV_CACHE_DIR=/home/christia50/Repos/ebook-reader/.uv-cache
export UV_PYTHON_PREFERENCE=only-system   # avoid a managed-Python download into a read-only dir
```

**This is a sandbox artifact, not part of the migration.** On a normal machine plain
`uv sync` / `uv run` use `~/.cache/uv` and need neither variable. `.uv-cache/` is ignored by
git and can be deleted at any time (it will be repopulated on the next `uv sync`; ~7.3 GB
because of the CUDA wheels).

---

## 11. Anything not verified

1. **Docker build / compose stack** — not run. `docker` is on PATH but the CUDA base image
   build (~GBs) and the daemon were not exercised, and starting containers is out of scope.
   The Dockerfile was deliberately left functionally unchanged, so risk is low, but the
   `requirements.txt`-based image build is **unverified**.
2. **`start.sh` end-to-end** — only syntax-checked (`bash -n`) and its environment-detection
   logic was exercised in isolation (with uv on `PATH` and off it). The script itself was not
   launched because it starts the backend/frontend servers, which this task forbids.
3. **The full test suite** — deliberately not run (slow and known-flaky). Only
   `tests/test_db_models.py` (6 tests) was used as a smoke test.
4. **GPU execution** — impossible in the sandbox (`/dev/nvidia*` absent). Only wheel
   provenance was verified (`torch.version.cuda == "13.0"`), not an actual CUDA kernel run.
5. **`uv sync` on a clean machine** — verified only with `UV_PYTHON_PREFERENCE=only-system`
   and a local cache. A normal `uv sync` will additionally download a managed CPython 3.12 if
   the host has none; that path was not exercised.
6. `torch.cuda.is_available()` remains `False` **in this sandbox** — expected, see §6.

---

## 12. How to add a dependency now

```bash
cd backend

uv add httpx                # runtime dependency -> [project.dependencies] + uv.lock
uv add --dev ruff           # dev/test dependency -> [dependency-groups].dev + uv.lock
uv remove pytest-cov        # remove one
uv lock --upgrade-package pymupdf   # bump a single package
uv sync                     # materialise backend/.venv from the lock

# keep the pip-compatibility file in step (required: backend/Dockerfile reads it)
uv export --format requirements-txt --no-hashes > requirements.txt
```

Rules of thumb:

- **Never** hand-edit `backend/requirements.txt`; it is generated.
- **Commit `backend/uv.lock`** with any dependency change (it is intentionally not gitignored).
- Run Python through uv from `backend/`: `uv run pytest -q`, `uv run uvicorn main:app --reload`;
  from the repo root use `uv run --project backend <cmd>`.
- `backend/venv` is the pre-migration environment; it still works and was left untouched,
  but new work should use `backend/.venv` via uv. Deleting `backend/venv` later is a human
  decision (other agents/environments may still reference it).
