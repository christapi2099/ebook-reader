"""Measured pitch check: does naive resampling preserve pitch? (No. WSOLA would.)

Strategy A naive form = synthesise at speed=1.0, then resample by 1/speed.
Resampling is a sample-rate change, so every frequency scales with the rate.
This measures median F0 by autocorrelation on the same sentence three ways:
  (1) Kokoro native speed=1.0
  (2) Kokoro native speed=1.5
  (3) Kokoro native speed=1.0, then polyphase resample to 1.5x shorter
If native speed drives the duration predictor (as model.py:108 shows), (2)
keeps the pitch of (1); the resampled (3) should sit ~1.5x higher.

Run:  backend/venv/bin/python docs/research/01-speed-strategy-pitch.py
"""
import numpy as np
from scipy.signal import resample_poly
from kokoro import KPipeline

SR = 24000
TEXT = "The quick brown fox jumps over the lazy dog near the riverbank."
pipe = KPipeline(lang_code="a")


def synth(speed):
    parts = []
    for r in pipe(TEXT, voice="af_heart", speed=speed):
        a = r.audio
        parts.append(a.numpy() if hasattr(a, "numpy") else np.asarray(a))
    return np.concatenate(parts).astype(np.float64)


def median_f0(y, sr=SR, fmin=60, fmax=400, win=0.040, hop=0.010):
    """Autocorrelation F0 on frames above an energy threshold; median of voiced."""
    w, h = int(win * sr), int(hop * sr)
    lo, hi = int(sr / fmax), int(sr / fmin)
    f0s = []
    for s in range(0, len(y) - w, h):
        fr = y[s:s + w]
        if np.sqrt(np.mean(fr ** 2)) < 0.02:
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[w - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:hi]
        if seg.size == 0 or seg.max() <= 0:
            continue
        lag = lo + int(np.argmax(seg))
        if ac[lag] / ac[0] < 0.3:
            continue
        f0s.append(sr / lag)
    return (float(np.median(f0s)), len(f0s)) if f0s else (float("nan"), 0)


a10 = synth(1.0)
a15 = synth(1.5)
# 3/2 upsampling of the 1.0x take = play it 1.5x faster by sample-rate change
a15_res = resample_poly(a10, 2, 3)

for name, y in (("native 1.0x", a10), ("native 1.5x", a15),
                ("1.0x resampled to 1.5x", a15_res)):
    f0, n = median_f0(y)
    print(f"{name:24s} samples={len(y):>6} audio_s={len(y)/SR:6.3f} median_F0={f0:7.2f} Hz voiced_frames={n}", flush=True)

f10, _ = median_f0(a10)
f15, _ = median_f0(a15)
fr, _ = median_f0(a15_res)
print(f"\nnative 1.5x F0 / native 1.0x F0   = {f15/f10:.3f}  (1.0 => pitch preserved)")
print(f"resampled F0 / native 1.0x F0     = {fr/f10:.3f}  (1.5 => pitch shifted up)")
print(f"pitch shift of resampling         = {12*np.log2(fr/f10):.1f} semitones")
print(f"pitch shift of native speed=1.5   = {12*np.log2(f15/f10):.2f} semitones")
