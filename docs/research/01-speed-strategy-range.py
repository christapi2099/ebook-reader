"""Probe the *actual* accepted range of Kokoro 0.9.4's `speed` kwarg.

No clamping exists in kokoro 0.9.4 (grep `speed` -> only model.py:108 divides).
This checks what happens outside the app's 0.5-3.0 UI window.

Run:  backend/venv/bin/python docs/research/01-speed-strategy-range.py
"""
import traceback
import numpy as np
from kokoro import KPipeline

TEXT = "The quick brown fox jumps over the lazy dog near the riverbank."
pipe = KPipeline(lang_code="a")

for sp in [0.25, 0.5, 3.0, 4.0, 6.0, 0.0, -1.0, float("nan"), float("inf")]:
    try:
        parts, frames = [], 0
        for r in pipe(TEXT, voice="af_heart", speed=sp):
            a = r.audio
            parts.append(a.numpy() if hasattr(a, "numpy") else np.asarray(a))
            if r.pred_dur is not None:
                frames += int(np.asarray(r.pred_dur).ravel().sum())
        n = int(len(np.concatenate(parts))) if parts else 0
        print(f"speed={sp!r:>6}  OK      frames={frames:>5} samples={n:>7} audio_s={n/24000:.3f}", flush=True)
    except Exception as e:
        print(f"speed={sp!r:>6}  RAISED  {type(e).__name__}: {e}", flush=True)
