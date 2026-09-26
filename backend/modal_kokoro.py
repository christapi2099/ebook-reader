"""Modal GPU deployment for Kokoro-82M speech synthesis.

Deploy with::

    modal deploy backend/modal_kokoro.py

The deployed ``synthesize`` function is consumed by
``backend/services/modal_remote.py``, which presents it to the rest of the
backend as a drop-in replacement for the local ``KPipeline`` object.  Nothing
in this file imports torch, kokoro or numpy at module level: those imports
happen inside the container so that ``modal deploy`` itself stays fast and the
local backend never needs this module to be importable.

Cost model
----------
The GPU is a *fallback list* (``L4, A10, T4``) rather than one pinned type,
because the right optimisation is cost per hour of **audio**, not cost per hour
of GPU (docs/research/kokoro-runtime-picks.md §4): L4 81x realtime at $0.80/h is
~$0.10 per 9-hour book, A10 96x at $1.10/h is ~$0.12, T4 36x at $0.59/h is
~$0.18.  Idle time, however, is what actually dominates a single reader's bill —
an L4 kept warm for a month is ~$575 — so the container is never kept warm:
``min_containers=0`` (scale to zero) with ``scaledown_window`` of
``MODAL_KOKORO_IDLE_SECONDS`` (default 60 s).  Billing therefore stops within a
minute of the last sentence, and the per-audio-hour figure is what is left.

No region is pinned, so Modal schedules wherever the chosen GPU is free.

Concurrency
-----------
``@modal.concurrent(max_inputs=4)`` lets one container take up to four
requests at once.  That is *not* batching: ``KModel.forward_with_tokens``
handles batch size 1 only, so a batch API would buy nothing.  What it does buy
is overlapping container start-up and volume/socket I/O; the forward pass itself
is serialised by ``_forward_lock`` because the pipeline is not re-entrant.

Wire contract (see ``services/modal_remote.py`` for the client half):

    request   {"texts": [str, ...], "voice": str, "speed": float,
               "lang_code": str}
    response  {"sample_rate": 24000, "results": [{"text": str, "chunks":
               [{"graphemes": str, "phonemes": str, "audio_b64": str,
                 "tokens": [...]}]}], ...}

``texts`` takes a list so one call can cover a whole chapter (the reader
currently sends one sentence per call).  ``audio_b64`` is base64 over
little-endian float32 mono PCM at 24000 Hz, which is exactly the dtype/rate
``TTSEngine`` already consumes.
"""

from __future__ import annotations

import base64
import io
import os
import threading
import time
from pathlib import Path
from typing import Any

import modal


def _positive_int(var: str, default: int) -> int:
    """A positive whole number from the environment, or ``default``.

    Deliberately local rather than ``services.env_config``: this is the file
    ``modal deploy`` uploads as the entrypoint of an image whose dependencies all
    come from ``pip_install``, and it imported nothing from this repository
    before. Depending on a sibling package would put the GPU deployment at the
    mercy of how Modal mounts local source — unverifiable from here, and a failed
    import there costs a paid app that will not start.

    Absent, unparsable, zero and negative all fall back: a bare
    ``int(os.environ[...])`` stopped the deployment from importing on one typo.
    """
    text = (os.environ.get(var) or "").strip()
    if not text:
        return default
    try:
        value = int(float(text))
    except (OverflowError, ValueError):
        print(f"[kokoro-modal] Invalid {var}={text!r}; using the default {default}")
        return default
    if value <= 0:
        print(f"[kokoro-modal] {var}={text!r} is not positive; using the default {default}")
        return default
    return value


APP_NAME = os.environ.get("MODAL_KOKORO_APP_NAME", "kokoro-tts")
FUNCTION_NAME = os.environ.get("MODAL_KOKORO_FUNCTION_NAME", "synthesize")
EXPORT_FUNCTION_NAME = os.environ.get("MODAL_KOKORO_EXPORT_FUNCTION_NAME", "synthesize_batch")
VOLUME_NAME = os.environ.get("MODAL_KOKORO_VOLUME_NAME", "kokoro-hf-cache")
# Cheapest per hour of audio first; see the module docstring. A single name
# ("T4") is still accepted and means "only that GPU".
GPU_FALLBACK = tuple(
    name.strip()
    for name in os.environ.get("MODAL_KOKORO_GPU", "L4,A10,T4").split(",")
    if name.strip()
)
IDLE_SECONDS = _positive_int("MODAL_KOKORO_IDLE_SECONDS", 60)
# Bulk export is compute-bound, not idle-bound, so it optimises the other way
# round from interactive reading: the cheapest GPU per synthesized hour, more
# containers at once, and an almost immediate scale-down.
EXPORT_GPU = os.environ.get("MODAL_KOKORO_EXPORT_GPU", "L4")
EXPORT_MAX_CONTAINERS = _positive_int("MODAL_KOKORO_EXPORT_MAX_CONTAINERS", 6)
EXPORT_IDLE_SECONDS = _positive_int("MODAL_KOKORO_EXPORT_IDLE_SECONDS", 5)
EXPORT_TIMEOUT_SECONDS = _positive_int("MODAL_KOKORO_EXPORT_TIMEOUT_SECONDS", 1800)
# Alpha Modal feature: off unless explicitly asked for, so the snapshot-free
# path is the supported and always-working one.
GPU_SNAPSHOT = os.environ.get("MODAL_KOKORO_GPU_SNAPSHOT", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
MAX_CONCURRENT_INPUTS = _positive_int("MODAL_KOKORO_MAX_INPUTS", 4)

SAMPLE_RATE = 24000
MODEL_REPO = "hexgrad/Kokoro-82M"
MODEL_WEIGHTS = "kokoro-v1_0.pth"
DEFAULT_VOICE = "af_heart"
DEFAULT_LANG_CODE = "a"

HF_CACHE_PATH = "/root/.cache/huggingface"
_HF_SNAPSHOT_GLOB = "models--hexgrad--Kokoro-82M"

_hf_cache = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


def _hf_snapshot_dir() -> Path:
    """Directory the Hugging Face hub writes the Kokoro snapshot into."""
    return Path(HF_CACHE_PATH) / "hub" / _HF_SNAPSHOT_GLOB


def _model_is_cached() -> bool:
    snapshot = _hf_snapshot_dir()
    if not snapshot.exists():
        return False
    return any(snapshot.glob(f"snapshots/*/{MODEL_WEIGHTS}"))


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def _runtime_image() -> modal.Image:
    """CUDA image with kokoro + the pronunciation front end, `HF_HOME` on a volume.

    ``espeak-ng`` is misaki's fallback grapheme-to-phoneme backend and is a
    hard requirement of the English pipeline; ``libsndfile1`` backs soundfile.
    """
    return (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("espeak-ng", "libsndfile1")
        .pip_install("kokoro==0.9.4", "soundfile", "numpy", "huggingface_hub")
        .env({"HF_HOME": HF_CACHE_PATH})
    )


def _bake_model_into_volume() -> None:
    """Populate the Hugging Face cache volume once, at image build time.

    Runs on CPU during ``modal deploy``; the resulting volume is mounted by the
    GPU function, so a cold container reads 355 MB of weights off the volume
    instead of re-downloading them from huggingface.co on every scale-up.
    """
    os.environ["HF_HUB_OFFLINE"] = "0"
    from huggingface_hub import hf_hub_download, list_repo_files

    hf_hub_download(repo_id=MODEL_REPO, filename="config.json")
    hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_WEIGHTS)
    voice_files = [f for f in list_repo_files(MODEL_REPO) if f.startswith("voices/")]
    for filename in voice_files:
        hf_hub_download(repo_id=MODEL_REPO, filename=filename)
    print(f"[kokoro-modal] baked {len(voice_files)} voices + {MODEL_WEIGHTS} into {HF_CACHE_PATH}")


image = _runtime_image().run_function(_bake_model_into_volume, volumes={HF_CACHE_PATH: _hf_cache})
# The weights are in the volume now; refusing network fetches at runtime keeps
# container start deterministic (and fails loudly instead of hanging) if the
# volume is ever missing.
image = image.env({"HF_HUB_OFFLINE": "1"})

# The health endpoint deliberately uses a torch-free image: it is public, so it
# must be cheap and fast to start. Modal requires FastAPI to be installed in the
# image of any function exposed with @modal.fastapi_endpoint.
health_image = modal.Image.debian_slim(python_version="3.12").pip_install("fastapi[standard]")

app = modal.App(APP_NAME)


# --------------------------------------------------------------------------
# In-container state
# --------------------------------------------------------------------------

_pipelines: dict[str, Any] = {}
# The Kokoro model is not re-entrant and handles one input at a time; with
# @modal.concurrent(max_inputs=4) up to four requests share this container, so
# every forward pass (and the one-off pipeline build) is serialised here.
_forward_lock = threading.Lock()


def _get_pipeline(lang_code: str) -> Any:
    """Return a warm ``KPipeline`` for ``lang_code``, building it on first use.

    Modal reuses a container for ``scaledown_window`` seconds, so the module
    global survives between calls and the 327 MB checkpoint is loaded once per
    container rather than once per sentence.
    """
    pipeline = _pipelines.get(lang_code)
    if pipeline is None:
        import torch
        from kokoro import KPipeline

        device = "cuda" if torch.cuda.is_available() else "cpu"
        started = time.perf_counter()
        pipeline = KPipeline(lang_code=lang_code, repo_id=MODEL_REPO, device=device)
        print(
            f"[kokoro-modal] loaded {MODEL_REPO} on {device} in "
            f"{(time.perf_counter() - started):.2f}s"
        )
        _pipelines[lang_code] = pipeline
    return pipeline


def _to_float32(audio: Any) -> Any:
    """Normalise whatever Kokoro yields (tensor, ndarray or list) to float32 PCM."""
    import numpy as np

    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    return np.asarray(audio, dtype="<f4").reshape(-1)


def _encode_tokens(result: Any) -> list[dict[str, Any]]:
    """Real word timings from Kokoro, so remote audio keeps local word highlighting."""
    tokens = getattr(result, "tokens", None) or []
    return [
        {
            "text": token.text,
            "phonemes": token.phonemes,
            "start_ts": token.start_ts,
            "end_ts": token.end_ts,
        }
        for token in tokens
    ]


def _synthesize_audio(text: str, voice: str, speed: float, pipeline: Any) -> Any:
    """Synthesize one string to a single float32 array.

    The pipeline handles batch size 1 only, so a "batch" call is simply this
    function applied to each text in turn while other requests wait on the lock.
    """
    import numpy as np

    parts = []
    with _forward_lock:
        for result in pipeline(text, voice=voice, speed=speed):
            parts.append(_to_float32(result.audio))
    if not parts:
        raise ValueError(f"Kokoro produced no audio for {len(text)} characters of text")
    return np.concatenate(parts) if len(parts) > 1 else parts[0]


def _render_one(text: str, voice: str, speed: float, pipeline: Any) -> list[dict[str, Any]]:
    """Synthesize one string, returning its wire-format chunks."""
    chunks: list[dict[str, Any]] = []
    with _forward_lock:
        for result in pipeline(text, voice=voice, speed=speed):
            audio = _to_float32(result.audio)
            chunks.append(
                {
                    "graphemes": result.graphemes,
                    "phonemes": result.phonemes,
                    "audio_b64": base64.b64encode(audio.tobytes()).decode("ascii"),
                    "samples": int(audio.size),
                    "tokens": _encode_tokens(result),
                }
            )
    if not chunks:
        raise ValueError(f"Kokoro produced no audio for {len(text)} characters of text")
    return chunks


def _render(payload: dict[str, Any]) -> dict[str, Any]:
    """Synthesize ``payload["texts"]`` and return the wire-format response."""
    texts = payload.get("texts")
    if texts is None:
        texts = [payload["text"]]
    if isinstance(texts, str):
        texts = [texts]
    if not texts:
        raise ValueError("no texts to synthesize")

    voice = payload.get("voice") or DEFAULT_VOICE
    speed = float(payload.get("speed", 1.0))
    lang_code = payload.get("lang_code") or DEFAULT_LANG_CODE

    # Held across the whole batch: the first call may still be loading the
    # checkpoint, and a second thread must not build a second 327 MB pipeline.
    with _forward_lock:
        pipeline = _get_pipeline(lang_code)

    started = time.perf_counter()
    results = [
        {"text": text, "chunks": _render_one(text, voice, speed, pipeline)} for text in texts
    ]

    import torch

    cuda = torch.cuda.is_available()
    return {
        "sample_rate": SAMPLE_RATE,
        "device": "cuda" if cuda else "cpu",
        "gpu": torch.cuda.get_device_name(0) if cuda else None,
        "voice": voice,
        "speed": speed,
        "results": results,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _render_batch(payload: dict[str, Any]) -> dict[str, Any]:
    """Render a batch of sentences and return them as one FLAC blob."""
    import numpy as np
    import soundfile as sf

    batch_index = int(payload.get("batch_index", 0))
    sentences = payload.get("sentences") or []
    if not sentences:
        raise ValueError(f"batch {batch_index} carries no sentences")

    voice = payload.get("voice") or DEFAULT_VOICE
    speed = float(payload.get("speed", 1.0))
    lang_code = payload.get("lang_code") or DEFAULT_LANG_CODE

    # Same warm pipeline as interactive synthesis: a batch and a play request
    # can share one container, and neither may build a second 327 MB checkpoint.
    with _forward_lock:
        pipeline = _get_pipeline(lang_code)

    started = time.perf_counter()
    parts: list[Any] = []
    for text in (sentence.get("text") or "" for sentence in sentences):
        if not text.strip():
            # Keep the sentence list and the sample-count list the same length,
            # so the caller can turn sample offsets into sentence offsets.
            parts.append(None)
            continue
        parts.append(_synthesize_audio(text, voice, speed, pipeline))

    if all(part is None for part in parts):
        raise ValueError(f"batch {batch_index} produced no audio")

    audio = np.concatenate([part for part in parts if part is not None]).astype("<f4", copy=False)
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="FLAC", subtype="PCM_16")

    return {
        "batch_index": batch_index,
        "sample_rate": SAMPLE_RATE,
        "samples": int(audio.size),
        # Per input sentence, in order: what the caller needs to place chapter
        # marks on the assembled file without guessing from word counts.
        "sentence_samples": [0 if part is None else int(part.size) for part in parts],
        "flac_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


# --------------------------------------------------------------------------
# Deployed functions
# --------------------------------------------------------------------------

def _deploy_options() -> dict[str, Any]:
    """Modal options shared by the GPU function, incl. the opt-in alpha snapshot."""
    options: dict[str, Any] = {
        "name": FUNCTION_NAME,
        "image": image,
        "gpu": list(GPU_FALLBACK),
        "volumes": {HF_CACHE_PATH: _hf_cache},
        "timeout": 300,
        # Scale to zero, always. Idle GPU time is the dominant cost, so the
        # container is only ever alive for scaledown_window seconds after the
        # last sentence.
        "min_containers": 0,
        "scaledown_window": IDLE_SECONDS,
        "max_containers": 4,
    }
    if GPU_SNAPSHOT:
        # Alpha: ~10-20 s cold start down to ~2-4 s when it works. Opt-in only;
        # the deployment below is fully functional with this switched off.
        options["enable_memory_snapshot"] = True
        options["experimental_options"] = {"enable_gpu_snapshot": True}
    return options


@app.function(**_deploy_options())
@modal.concurrent(max_inputs=MAX_CONCURRENT_INPUTS)
def synthesize(payload: dict[str, Any]) -> dict[str, Any]:
    """GPU Kokoro synthesis. Called over the Modal SDK by the local backend."""
    return _render(payload)


def _export_options() -> dict[str, Any]:
    """Options for the bulk-export function.

    Export is compute-bound rather than idle-bound, so it asks for the cheapest
    GPU *per synthesized hour* (L4 by default) instead of the interactive
    fallback list, runs more containers at once, and scales down almost
    immediately: by the time a batch finishes, the next one is already queued.
    """
    options = _deploy_options()
    options["name"] = EXPORT_FUNCTION_NAME
    options["gpu"] = [g for g in EXPORT_GPU.split(",") if g.strip()]
    options["max_containers"] = EXPORT_MAX_CONTAINERS
    options["scaledown_window"] = EXPORT_IDLE_SECONDS
    options["timeout"] = EXPORT_TIMEOUT_SECONDS
    options.pop("min_containers", None)
    return options


@app.function(**_export_options())
def synthesize_batch(payload: dict[str, Any]) -> dict[str, Any]:
    """Synthesize a whole batch of sentences and return it as one FLAC blob.

    One call per chapter-sized batch means one container start per batch instead
    of one per sentence, and ``.map()`` on the client fans the batches out over
    several containers at once. FLAC rather than raw float32 because a ten-minute
    batch is ~10-15 MB that way instead of ~60 MB, and it round-trips to exactly
    the same sample count.
    """
    return _render_batch(payload)


@app.function(image=health_image)
@modal.fastapi_endpoint(method="GET", label="health")
def health() -> dict[str, Any]:
    """Public, torch-free liveness probe (CPU only, a few milliseconds).

    It intentionally does *not* start a GPU container: an internet-reachable
    endpoint that spins up a GPU container would be a standing cost risk.
    """
    return {
        "status": "ok",
        "app": APP_NAME,
        "function": FUNCTION_NAME,
        "export_function": EXPORT_FUNCTION_NAME,
        "gpu": ",".join(GPU_FALLBACK),
        "export_gpu": EXPORT_GPU,
        "max_inputs": MAX_CONCURRENT_INPUTS,
        "sample_rate": SAMPLE_RATE,
    }


@app.local_entrypoint()
def main(text: str = "The quick brown fox jumps over the lazy dog.", voice: str = DEFAULT_VOICE) -> None:
    """Smoke-test the deployed function: ``modal run backend/modal_kokoro.py``."""
    result = synthesize.remote({"texts": [text], "voice": voice, "speed": 1.0})
    chunks = result["results"][0]["chunks"]
    total_samples = sum(chunk["samples"] for chunk in chunks)
    print(
        f"{result['device']} ({result['gpu']}): {len(chunks)} chunk(s), "
        f"{total_samples} samples = {total_samples / result['sample_rate']:.2f}s, "
        f"{result['elapsed_ms']} ms"
    )
