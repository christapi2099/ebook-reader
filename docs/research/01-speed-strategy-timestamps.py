"""Verify that Kokoro's word timestamps are already speed-adjusted.

tts_engine._collect_result reads `t.start_ts` / `t.end_ts`, populated by
KPipeline.join_timestamps(tks, output.pred_dur) (pipeline.py:284-320), which
counts *post-division* frames. If so, the timestamps must shrink with speed,
and the last token's end_ts must not exceed the audio duration.

Run:  backend/venv/bin/python docs/research/01-speed-strategy-timestamps.py
"""
import numpy as np
from kokoro import KPipeline

TEXT = "The quick brown fox jumps over the lazy dog near the riverbank."
pipe = KPipeline(lang_code="a")

for sp in (1.0, 1.5, 2.0, 3.0):
    for r in pipe(TEXT, voice="af_heart", speed=sp):
        a = r.audio
        y = a.numpy() if hasattr(a, "numpy") else np.asarray(a)
        dur = len(y) / 24000
        w = [(t.text, round(t.start_ts, 4), round(t.end_ts, 4))
             for t in (r.tokens or []) if t.phonemes and any(c.isalnum() for c in t.text)]
        print(f"\n=== speed={sp}  audio_s={dur:.3f}  words={len(w)}  pred_dur_frames={int(np.asarray(r.pred_dur).ravel().sum())}")
        print("  first 3:", w[:3])
        print("  last  3:", w[-3:])
        print(f"  last_word_end_ts={w[-1][2] if w else None}  audio_s={dur:.3f}  "
              f"slack={None if not w else round(dur - w[-1][2], 4)}")
