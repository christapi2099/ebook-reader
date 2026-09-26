"""Pure arithmetic for docs/research/02-synthesis-strategy.md.

Inputs are the DB measurements (read-only) plus the UI's speed/voice catalog
parsed from the frontend. No writes anywhere.

Run: cd backend && venv/bin/python ../docs/research/calc_arithmetic.py
"""
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DB = REPO / "backend" / "ebook_reader.db"

# ---------------------------------------------------------------- DB measures
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()
n_rows, total_bytes, avg_bytes, min_b, max_b = cur.execute(
    "SELECT COUNT(*), SUM(LENGTH(audio_data)), AVG(LENGTH(audio_data)), "
    "MIN(LENGTH(audio_data)), MAX(LENGTH(audio_data)) FROM audiocache"
).fetchone()
avg_dur_ms, total_dur_ms = cur.execute(
    "SELECT AVG(duration_ms), SUM(duration_ms) FROM audiocache"
).fetchone()
n_books = cur.execute("SELECT COUNT(*) FROM book").fetchone()[0]
n_voices = cur.execute("SELECT COUNT(DISTINCT voice) FROM audiocache").fetchone()[0]
ts_bytes = cur.execute(
    "SELECT SUM(LENGTH(word_timestamps)) FROM audiocache WHERE word_timestamps IS NOT NULL"
).fetchone()[0]
# sentence counts for the one large real book
book_rows = cur.execute(
    """SELECT b.title, b.page_count, COUNT(s.id),
              SUM(CASE WHEN s.filtered=0 THEN 1 ELSE 0 END)
       FROM book b LEFT JOIN sentence s ON s.book_id=b.id
       GROUP BY b.id ORDER BY 2 DESC"""
).fetchall()
avg_chars = cur.execute(
    "SELECT AVG(LENGTH(text)) FROM sentence WHERE filtered=0"
).fetchone()[0]
con.close()

SAMPLE_RATE = 24000
BYTES_PER_SAMPLE = 2  # int16 mono PCM, confirmed by schema + writer code
BYTES_PER_AUDIO_S = SAMPLE_RATE * BYTES_PER_SAMPLE

# ------------------------------------------------------- UI catalog (frontend)
# frontend/src/lib/components/MediaBar.svelte:24
UI_SPEEDS_7 = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]
# task brief's hypothetical granular grid
UI_SPEEDS_11 = [round(0.5 + 0.25 * i, 2) for i in range(11)]
# backend/routers/voices.py ENGLISH_VOICE_CATALOG
UI_VOICES = 28

FREE_BYTES = 529 * 1024**3  # df -h reported 529G available

# --------------------------------------------------------- bench-measured rate
bench = json.loads((HERE / "bench_synth.json").read_text())
S = bench["summary"]
CPU_WALL = S["warm_mean_wall_s"]
CPU_RTF = S["warm_mean_rtf"]

# measured chars -> seconds of audio at 1.0x, from the bench rows
rows = [t for t in bench["timings"] if t["warm"] and t["chars"] >= 20 and t["audio_s"] > 0]
CHARS_PER_AUDIO_S = sum(t["chars"] for t in rows) / sum(t["audio_s"] for t in rows)

out = {}
out["db"] = {
    "rows": n_rows,
    "total_audio_bytes": total_bytes,
    "avg_bytes_per_row": round(avg_bytes, 1),
    "min_bytes": min_b,
    "max_bytes": max_b,
    "avg_duration_ms": round(avg_dur_ms, 1),
    "avg_duration_s": round(avg_dur_ms / 1000, 3),
    "total_audio_hours": round(total_dur_ms / 1000 / 3600, 3),
    "distinct_books_with_sentences": n_books,
    "distinct_voices_in_cache": n_voices,
    "word_timestamp_bytes": ts_bytes,
    "db_file_bytes": DB.stat().st_size,
    "derived_bytes_per_audio_s": total_bytes / (total_dur_ms / 1000),
    "expected_bytes_per_audio_s": BYTES_PER_AUDIO_S,
    "avg_chars_per_speakable_sentence": round(avg_chars, 1),
    "books": [
        {"title": t, "pages": p, "sentences": n, "speakable": sp}
        for t, p, n, sp in book_rows
    ],
}

# ----------------------------------------------------- 300-page book estimate
clean = next(r for r in book_rows if r[0] == "cleancodebook.pdf")
sent_per_page = clean[2] / clean[1]
speak_ratio = clean[3] / clean[2]
PAGES = 300
sent_300 = PAGES * sent_per_page
speak_300 = sent_300 * speak_ratio
out["book_300pp"] = {
    "source_book": clean[0],
    "source_pages": clean[1],
    "source_sentences": clean[2],
    "source_speakable": clean[3],
    "sentences_per_page": round(sent_per_page, 2),
    "speakable_ratio": round(speak_ratio, 4),
    "sentences_300pp": round(sent_300),
    "speakable_sentences_300pp": round(speak_300),
    "crosscheck_words": round(speak_300 * avg_chars / 5.9),
}
out["chars_per_audio_s_1x_measured"] = round(CHARS_PER_AUDIO_S, 2)

# audio seconds per sentence, two independent estimates
aud_s_db = avg_dur_ms / 1000                       # DB blend (speed unknown!)
aud_s_1x = avg_chars / CHARS_PER_AUDIO_S           # bench-derived at 1.0x
out["audio_seconds_per_sentence"] = {
    "db_blend_measured": round(aud_s_db, 3),
    "bench_derived_at_1x": round(aud_s_1x, 3),
    "implied_blend_speed": round(aud_s_1x / aud_s_db, 2),
}

N = round(speak_300)

# ------------------------------------------------------------- disk per book
def book_bytes(audio_s_per_sentence, speed):
    return N * audio_s_per_sentence / speed * BYTES_PER_AUDIO_S

def gib(b):
    return round(b / 1024**3, 2)

d = {}
for label, a in (("db_blend", aud_s_db), ("bench_1x", aud_s_1x)):
    d[label] = {
        "book_bytes_at_1_0x": round(book_bytes(a, 1.0)),
        "book_gib_at_1_0x": gib(book_bytes(a, 1.0)),
        "seventeen_speed_sum_factor_7": round(sum(1 / s for s in UI_SPEEDS_7), 4),
        "all_7_speeds_gib": gib(book_bytes(a, 1.0) * sum(1 / s for s in UI_SPEEDS_7)),
        "eleven_speed_sum_factor_11": round(sum(1 / s for s in UI_SPEEDS_11), 4),
        "all_11_speeds_gib": gib(book_bytes(a, 1.0) * sum(1 / s for s in UI_SPEEDS_11)),
    }
    d[label]["all_7_speeds_x_28_voices_gib"] = gib(
        book_bytes(a, 1.0) * sum(1 / s for s in UI_SPEEDS_7) * UI_VOICES)
    d[label]["all_11_speeds_x_28_voices_gib"] = gib(
        book_bytes(a, 1.0) * sum(1 / s for s in UI_SPEEDS_11) * UI_VOICES)
    d[label]["all_7_speeds_x_28_voices_x_7_books_gib"] = gib(
        book_bytes(a, 1.0) * sum(1 / s for s in UI_SPEEDS_7) * UI_VOICES * 7)
out["disk"] = d
out["free_disk_gib"] = round(FREE_BYTES / 1024**3, 1)
out["free_disk_gb"] = round(FREE_BYTES / 1000**3, 1)

# ---------------------------------------------------------------- time to build
t = {}
for label, a in (("db_blend", aud_s_db), ("bench_1x", aud_s_1x)):
    audio_s = N * a
    t[label] = {
        "audio_hours_at_1_0x": round(audio_s / 3600, 2),
        # measured CPU, this host
        "cpu_wall_hours_one_pass": round(N * CPU_WALL / 3600, 2),
        # ESTIMATED GPU. Wide range: T4 (also Turing cc 7.5) measured at 36x realtime
        # PyTorch CUDA, scaled down for the T600's smaller die -> ~11-20x. But the only
        # directly measured 4 GB-VRAM datapoint (ebook2audiobook, XTTSv2) is ~1x realtime.
        "gpu_est_realtime_factor_low": 1.0,
        "gpu_est_realtime_factor_high": 20.0,
        "gpu_est_wall_hours_one_pass_pessimistic_1x": round(N * a / 1.0 / 3600, 2),
        "gpu_est_wall_minutes_one_pass_likely_11x": round(N * a / 11.0 / 60, 1),
        "gpu_est_wall_minutes_one_pass_optimistic_20x": round(N * a / 20.0 / 60, 1),
    }
    # every speed change = a fresh pass over the whole book
    t[label]["cpu_wall_hours_11_speed_changes_1_voice"] = round(
        N * CPU_WALL / 3600 * len(UI_SPEEDS_11), 2)
    t[label]["gpu_est_hours_11_speed_changes_1_voice_at_11x"] = round(
        N * a / 11.0 / 3600 * len(UI_SPEEDS_11), 2)
out["time"] = t
out["measured_cpu_per_sentence_s"] = CPU_WALL
out["measured_cpu_rtf"] = CPU_RTF
out["measured_cpu_rttf_median"] = S["warm_median_wall_s"]

# ------------------------------------------------------- existing cache growth
out["growth"] = {
    "note": "AudioCache has no eviction; every new (text,voice,speed) is a permanent row",
    "rows_added_per_full_book_pass": N,
    "bytes_added_per_full_book_pass_gib_dbblend": gib(book_bytes(aud_s_db, 1.0)),
    "three_hours_listening_at_1_5x_sentences": round(3 * 3600 / (aud_s_db * 1.5)),
}
# cost of a single speed nudge with a 50-sentence prefetch
for tmp in (1.0, 1.5):
    pass
out["prefetch_reality_check"] = {
    "current_prefetch_count": 50,
    "audio_seconds_warmed_dbblend": round(50 * aud_s_db, 1),
    "wall_seconds_to_warm_measured_cpu": round(50 * CPU_WALL, 1),
    "can_cpu_keep_up_at_1x": CPU_RTF < 1.0,
    "can_cpu_keep_up_at_1_5x": CPU_RTF < 1.5,
    "cpu_rtf": CPU_RTF,
}

(HERE / "arithmetic.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
