"""Addendum: (a) verify the `clamp(min=1)` duration-floor mechanism, and
(b) measure CPU cost vs `speed` with a contention-tolerant metric.

(a) `model.py:108-109` in kokoro 0.9.4:
        duration = torch.sigmoid(duration).sum(axis=-1) / speed
        pred_dur = torch.round(duration).clamp(min=1).long().squeeze()
    Every token -- including BOS/EOS -- keeps at least 1 frame
    (600 samples = 25 ms at 24 kHz), so the achieved speedup should saturate
    below the requested `speed`.

(b) This box is heavily loaded by processes outside the sandbox (load average
    ~41 on 16 cores), so wall-clock is not trustworthy. `time.process_time()`
    sums CPU time across threads and is much less sensitive to contention.

Run:  backend/venv/bin/python docs/research/01-speed-strategy-durationmodel.py
"""
import json
import statistics
import time

import numpy as np
from kokoro import KPipeline

TEXT = "The quick brown fox jumps over the lazy dog near the riverbank."
SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]
REPEATS = 2

pipe = KPipeline(lang_code="a")


def synth(speed):
    cpu0, wall0 = time.process_time(), time.perf_counter()
    parts, frames, dv = [], 0, None
    for r in pipe(TEXT, voice="af_heart", speed=speed):
        a = r.audio
        parts.append(a.numpy() if hasattr(a, "numpy") else np.asarray(a))
        if r.pred_dur is not None:
            dv = np.asarray(r.pred_dur).ravel()
            frames += int(dv.sum())
    audio = np.concatenate(parts)
    return (time.process_time() - cpu0, time.perf_counter() - wall0,
            int(len(audio)), frames, dv)


rows = []
vectors = {}
for sp in SPEEDS:
    cpu_t, wall_t, samples, frames, dv = [], [], [], [], None
    for _ in range(REPEATS):
        c, w, n, f, d = synth(sp)
        cpu_t.append(c); wall_t.append(w); samples.append(n); frames.append(f)
        dv = d if d is not None else dv
    vectors[str(sp)] = None if dv is None else dv.tolist()
    rows.append({
        "speed": sp,
        "frames": frames[0],
        "samples": samples[0],
        "audio_s": round(samples[0] / 24000, 4),
        "cpu_s_median": round(statistics.median(cpu_t), 3),
        "wall_s_median": round(statistics.median(wall_t), 3),
        "cpu_s_per_audio_s": round(statistics.median(cpu_t) / (samples[0] / 24000), 3),
        "tokens": None if dv is None else int(len(dv)),
        "min_token_frames": None if dv is None else int(dv.min()),
        "median_token_frames": None if dv is None else float(np.median(dv)),
    })
    print(json.dumps(rows[-1]), flush=True)

base = next(r for r in rows if r["speed"] == 1.0)
d1 = np.array(vectors["1.0"])
print("--- token duration vector at speed=1.0 ---", flush=True)
print(json.dumps({"n_tokens": int(d1.size), "sum": int(d1.sum()),
                  "vector": d1.tolist()}), flush=True)

print("--- analytic sum(max(1, round(d_i/speed))) vs measured ---", flush=True)
model_check = []
for sp in SPEEDS:
    predicted = int(np.maximum(1, np.round(d1 / sp)).sum())
    measured = next(r["frames"] for r in rows if r["speed"] == sp)
    eff = round(base["frames"] / measured, 3)
    model_check.append({
        "speed": sp, "predicted_frames": predicted, "measured_frames": measured,
        "effective_speedup": eff, "requested": sp,
        "shortfall_pct": round((1 - eff / sp) * 100, 1),
        "predicted_effective_speedup": round(base["frames"] / predicted, 3),
    })
    print(json.dumps(model_check[-1]), flush=True)

print("--- CPU cost normalised to speed=1.0 (cpu_s per audio second) ---", flush=True)
for r in rows:
    print(json.dumps({"speed": r["speed"], "cpu_s_per_audio_s": r["cpu_s_per_audio_s"],
                      "cpu_ratio_vs_1x": round(r["cpu_s_per_audio_s"] / base["cpu_s_per_audio_s"], 3),
                      "cpu_s_per_sentence": r["cpu_s_median"],
                      "frames": r["frames"]}), flush=True)

with open("docs/research/01-speed-strategy-durationmodel.json", "w") as f:
    json.dump({"rows": rows, "model_check": model_check,
               "token_dur_at_1x": d1.tolist()}, f, indent=2)
print("wrote docs/research/01-speed-strategy-durationmodel.json", flush=True)
