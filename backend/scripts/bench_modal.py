"""Benchmark the deployed Kokoro-82M Modal app on real book text.

    cd backend && .venv/bin/python scripts/bench_modal.py PDF --first-page 26 --last-page 67 \
        [--out ../docs/research/modal-kokoro-bench.json] [--parallel 1,4,8]

Measures what decides whether live streaming and parallel prerender are viable:
  * time to first audio for one sentence (cold start, then warm)
  * per-sentence latency and real-time factor over a warm run
  * one multi-sentence request
  * a whole chapter split into K batches across K containers (the prerender path)

Uses the same client the app uses (services.modal_remote.ModalKokoroClient) and
your ~/.modal.toml profile. It spends real GPU time (cents); it writes nothing
to the app database.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SR = 24000


def chapter_sentences(pdf: str, first: int, last: int) -> list[str]:
    import pymupdf

    doc = pymupdf.open(pdf)
    text = " ".join(doc[i].get_text() for i in range(first, last + 1))
    text = re.sub(r"-\n(?=[a-z])", "", text)  # hyphenated line breaks
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", text)
    return [p.strip() for p in parts if 20 <= len(p.strip()) <= 400]


def audio_seconds(groups) -> float:
    return sum(len(chunk[-1]) for group in groups for chunk in group) / SR


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--first-page", type=int, default=26, help="0-based index")
    ap.add_argument("--last-page", type=int, default=67)
    ap.add_argument("--parallel", default="1,4,8")
    ap.add_argument("--singles", type=int, default=20)
    ap.add_argument("--out")
    args = ap.parse_args()

    from services.modal_remote import ModalKokoroClient

    sentences = chapter_sentences(args.pdf, args.first_page, args.last_page)
    print(f"{len(sentences)} sentences, {sum(map(len, sentences))} chars")
    client = ModalKokoroClient()
    print("transport:", client.transport, "| warm before:", client.is_warm())
    result: dict = {"sentences": len(sentences), "chars": sum(map(len, sentences))}

    # 1. first call (cold if the container is down), then a second (warm)
    t = time.perf_counter()
    g = client.synthesize_many([sentences[0]])
    first = time.perf_counter() - t
    t = time.perf_counter()
    g2 = client.synthesize_many([sentences[1]])
    second = time.perf_counter() - t
    result["first_call_s"] = round(first, 2)
    result["second_call_s"] = round(second, 2)
    print(f"first call {first:.2f}s (audio {audio_seconds(g):.1f}s) | second {second:.2f}s "
          f"(audio {audio_seconds(g2):.1f}s)")

    # 2. warm single-sentence latency
    lat, aud = [], []
    for s in sentences[2 : 2 + args.singles]:
        t = time.perf_counter()
        grp = client.synthesize_many([s])
        lat.append(time.perf_counter() - t)
        aud.append(audio_seconds(grp))
    result["single"] = {
        "n": len(lat),
        "median_s": round(statistics.median(lat), 3),
        "p95_s": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 3),
        "rtf": round(sum(lat) / sum(aud), 3),
        "audio_s": round(sum(aud), 1),
    }
    print("single-sentence:", result["single"])

    # 3. one request with 10 sentences
    t = time.perf_counter()
    grp = client.synthesize_many(sentences[22:32])
    dt = time.perf_counter() - t
    result["multi10"] = {"wall_s": round(dt, 2), "audio_s": round(audio_seconds(grp), 1),
                         "rtf": round(dt / audio_seconds(grp), 3)}
    print("10-sentence request:", result["multi10"])

    # 4. whole chapter as K parallel requests on the interactive function. The
    # deployed app may predate `synthesize_batch`, so this uses `synthesize.map`
    # (one request per container slice) which exists on every deployment.
    import modal
    from services.modal_remote import decode_results

    fn = modal.Function.from_name(client.config.app_name, client.config.function_name)
    result["parallel"] = {}
    for k in [int(x) for x in args.parallel.split(",")]:
        size = -(-len(sentences) // k)
        slices = [sentences[i * size : (i + 1) * size] for i in range(k) if sentences[i * size : (i + 1) * size]]
        payloads = [client.build_payload(sl, "af_heart", 1.0) for sl in slices]
        t = time.perf_counter()
        first_done, total_audio = None, 0.0
        for resp in fn.map(payloads, order_outputs=False):
            if first_done is None:
                first_done = time.perf_counter() - t
            total_audio += audio_seconds(decode_results(resp, client.config))
        wall = time.perf_counter() - t
        result["parallel"][k] = {
            "requests": len(slices), "wall_s": round(wall, 1), "first_done_s": round(first_done or 0, 1),
            "audio_s": round(total_audio, 1), "rtf": round(wall / total_audio, 4),
            "speed_x": round(total_audio / wall, 1),
        }
        print(f"K={k}:", result["parallel"][k])

    print(json.dumps(result, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
