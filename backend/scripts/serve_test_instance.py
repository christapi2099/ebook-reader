"""Run a throwaway backend (plus optional frontend preview) from ANY worktree, isolated from prod.

    # GPU branch on the AMD env, engine GPU, fresh DB, own renders dir, port 8012:
    backend/scripts/serve_test_instance.py --worktree .worktrees/gpu-synth-workers --port 8012 \
        --python backend/.venv-rocm/bin/python --engine gpu --rocm [--frontend-build DIR --preview-port 4175]

State (DB, renders, uploads, logs) lives in --data (default /tmp/ebook-test-<port>), never in the repo or in
the production backend's folders. Prod (port 8000) and its preview (4173) are not touched. The render
worker is ON (so books actually render); pass --no-render for a read-only copy. Stop with --stop.
Prints the URLs. Rerunnable: an existing --data dir is reused (delete it for a fresh start).
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def pids(port: int) -> list[int]:
    """Our own uvicorn/vite processes on ``port`` (matched by program, so a shell whose command
    line merely mentions the port, or this script, is never selected)."""
    out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines()[1:]:
        pid, _, args = line.strip().partition(" ")
        if f"--port {port}" in args and ("uvicorn main:app" in args or "vite.js preview" in args) \
                and int(pid) != os.getpid():
            found.append(int(pid))
    return found


def wait_http(url: str, secs: int = 90) -> bool:
    end = time.time() + secs
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=3).read(1)
            return True
        except Exception:
            time.sleep(1)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--worktree", default=".", help="checkout whose backend code to run")
    ap.add_argument("--port", type=int, default=8012)
    ap.add_argument("--python", default=str(REPO / "backend/.venv/bin/python"))
    ap.add_argument("--engine", default=None, help="cpu|gpu|piper|kokoro-onnx|modal (saved in the test DB)")
    ap.add_argument("--rocm", action="store_true", help="export HSA_OVERRIDE_GFX_VERSION=10.3.0")
    ap.add_argument("--data", default=None)
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--frontend-build", default=None, help="dir holding a built frontend (vite preview)")
    ap.add_argument("--preview-port", type=int, default=4175)
    ap.add_argument("--stop", action="store_true")
    a = ap.parse_args()
    wt = Path(a.worktree).resolve()
    data = Path(a.data or f"/tmp/ebook-test-{a.port}")
    if a.stop:
        for p in pids(a.port) + pids(a.preview_port):
            os.kill(p, signal.SIGTERM)
        print("stopped test instance on", a.port, "/", a.preview_port)
        return
    if a.port in (8000, 4173) or a.preview_port in (8000, 4173):
        sys.exit("refusing to use the production ports 8000/4173")
    if pids(a.port):
        sys.exit(f"a test backend already runs on port {a.port}; use --stop first")
    for d in ("renders", "uploads", "covers", "render-cache"):
        (data / d).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "DB_PATH": str(data / "ebook_reader.db"), "RENDERS_DIR": str(data / "renders"),
           "RENDER_CACHE_DIR": str(data / "render-cache"), "UPLOADS_DIR": str(data / "uploads"),
           "COVERS_DIR": str(data / "covers"), "PROCESSOR_ENABLED": "0" if a.no_render else "1"}
    if a.rocm:
        env["HSA_OVERRIDE_GFX_VERSION"] = "10.3.0"
    log = open(data / "backend.log", "ab")
    cmd = [a.python, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(a.port)]
    subprocess.Popen(cmd, cwd=wt / "backend", env=env, stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    if not wait_http(f"http://127.0.0.1:{a.port}/library"):
        sys.exit(f"backend did not come up; see {data / 'backend.log'}")
    if a.engine:  # saved choice only takes effect after a restart (engine_manager.request_switch)
        req = urllib.request.Request(f"http://127.0.0.1:{a.port}/api/system/engine", method="POST",
                                     data=f'{{"engine":"{a.engine}"}}'.encode(),
                                     headers={"content-type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
        for p in pids(a.port):
            os.kill(p, signal.SIGTERM)
        time.sleep(4)
        subprocess.Popen(cmd, cwd=wt / "backend", env=env, stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        if not wait_http(f"http://127.0.0.1:{a.port}/library"):
            sys.exit(f"backend did not restart; see {data / 'backend.log'}")
    print(f"backend  http://127.0.0.1:{a.port}   data {data}   log {data / 'backend.log'}")
    if a.frontend_build:
        fe = Path(a.frontend_build)
        penv = {**os.environ, "BACKEND_URL": f"http://127.0.0.1:{a.port}"}
        subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "preview", "--host", "127.0.0.1",
                          "--port", str(a.preview_port), "--strictPort"], cwd=fe, env=penv,
                         stdout=open(data / "preview.log", "ab"), stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        if wait_http(f"http://127.0.0.1:{a.preview_port}/"):
            print(f"frontend http://127.0.0.1:{a.preview_port}")


if __name__ == "__main__":
    main()
