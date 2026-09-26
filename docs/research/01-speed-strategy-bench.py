"""Benchmark: Kokoro-82M wall-clock + sample counts vs `speed` (CPU-only sandbox).

Run:  backend/venv/bin/python docs/research/01-speed-strategy-bench.py
Artifact for docs/research/01-speed-strategy.md. Not part of the app.
"""
import time, json, statistics, sys
import numpy as np
import torch
from kokoro import KPipeline

SENT = "The quick brown fox jumps over the lazy dog near the riverbank."
LONG = ("Reading aloud is a strange and wonderful thing, because the voice in your head "
        "is never quite the same as the voice on the page, and a good narrator can make "
        "even a telephone directory sound like a confession.")
SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]
REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 1

print("device cuda available:", torch.cuda.is_available(), flush=True)
t0 = time.perf_counter()
pipe = KPipeline(lang_code="a")
print(f"pipeline init: {time.perf_counter()-t0:.2f}s", flush=True)

def run(text, speed):
    t = time.perf_counter()
    frames = 0
    parts = []
    for r in pipe(text, voice="af_heart", speed=speed):
        a = r.audio
        a = a.numpy() if hasattr(a, "numpy") else np.asarray(a)
        parts.append(a)
        if r.pred_dur is not None:
            frames += int(r.pred_dur.sum())
    el = time.perf_counter() - t
    audio = np.concatenate(parts) if parts else np.array([])
    return el, len(audio), frames

out = {"device": "cpu (sandbox: /dev/nvidia* not exposed)", "torch": torch.__version__,
       "kokoro_samples": []}
for label, text in (("short", SENT), ("long", LONG)):
    for sp in SPEEDS:
        times, samples, frames = [], [], []
        for _ in range(REPEATS):
            el, n, fr = run(text, sp)
            times.append(el); samples.append(n); frames.append(fr)
        row = {"text": label, "speed": sp, "wall_s": round(statistics.median(times), 4),
               "samples": samples[0], "audio_s": round(samples[0]/24000, 4),
               "frames": frames[0],
               "frames_per_phoneme_proxy": None,
               "rtf": round(statistics.median(times) / (samples[0]/24000), 4)}
        out["kokoro_samples"].append(row)
        print(json.dumps(row), flush=True)

# Compare: 1.0x synthesis + linear resample to the target speed
print("--- strategy A vs B on audio length (resample is pure DSP, no model) ---", flush=True)
base = [r for r in out["kokoro_samples"] if r["text"] == "long" and r["speed"] == 1.0][0]
for sp in SPEEDS:
    b = [r for r in out["kokoro_samples"] if r["text"] == "long" and r["speed"] == sp][0]
    print(json.dumps({"speed": sp, "native_audio_s": b["audio_s"],
                      "resampled_audio_s": round(base["audio_s"]/sp, 4),
                      "native_frames": b["frames"], "speed1_frames": base["frames"]}), flush=True)

with open("docs/research/01-speed-strategy-bench.json", "w") as f:
    json.dump(out, f, indent=2)
print("wrote docs/research/01-speed-strategy-bench.json", flush=True)
