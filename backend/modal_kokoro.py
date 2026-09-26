"""Modal GPU deployment for Kokoro-82M speech synthesis.

Deploy with::

    modal deploy backend/modal_kokoro.py

The deployed ``synthesize`` function is consumed by
``backend/services/modal_remote.py``, which presents it to the rest of the
backend as a drop-in replacement for the local ``KPipeline`` object.  Nothing
in this file imports torch, kokoro or numpy at module level: those imports
happen inside the container so that ``modal deploy`` itself stays fast and the
local backend never needs this module to be importable.

Wire contract (see ``services/modal_remote.py`` for the client half):

    request   {"text": str, "voice": str, "speed": float, "lang_code": str}
    response  {"sample_rate": 24000, "chunks": [{"graphemes": str,
               "phonemes": str, "audio_b64": str, "tokens": [...]}], ...}

``audio_b64`` is base64 over little-endian float32 mono PCM at 24000 Hz, which
is exactly the dtype/rate ``TTSEngine`` already consumes.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import modal

APP_NAME = os.environ.get("MODAL_KOKORO_APP_NAME", "kokoro-tts")
FUNCTION_NAME = os.environ.get("MODAL_KOKORO_FUNCTION_NAME", "synthesize")
VOLUME_NAME = os.environ.get("MODAL_KOKORO_VOLUME_NAME", "kokoro-hf-cache")
# T4 is the cheapest GPU Modal offers (~$0.59/h) and is ample for an 82M model.
# Override with e.g. MODAL_KOKORO_GPU=L4 (~$0.80/h) for faster cold starts.
GPU_TYPE = os.environ.get("MODAL_KOKORO_GPU", "T4")
IDLE_SECONDS = int(os.environ.get("MODAL_KOKORO_IDLE_SECONDS", "60"))

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


def _render(payload: dict[str, Any]) -> dict[str, Any]:
    """Synthesize ``payload["text"]`` and return the wire-format response."""
    text = payload["text"]
    voice = payload.get("voice") or DEFAULT_VOICE
    speed = float(payload.get("speed", 1.0))
    lang_code = payload.get("lang_code") or DEFAULT_LANG_CODE

    pipeline = _get_pipeline(lang_code)
    started = time.perf_counter()

    chunks: list[dict[str, Any]] = []
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

    import torch

    return {
        "sample_rate": SAMPLE_RATE,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "voice": voice,
        "speed": speed,
        "chunks": chunks,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


# --------------------------------------------------------------------------
# Deployed functions
# --------------------------------------------------------------------------

@app.function(
    name=FUNCTION_NAME,
    image=image,
    gpu=GPU_TYPE,
    volumes={HF_CACHE_PATH: _hf_cache},
    timeout=300,
    scaledown_window=IDLE_SECONDS,
    max_containers=4,
)
def synthesize(payload: dict[str, Any]) -> dict[str, Any]:
    """GPU Kokoro synthesis. Called over the Modal SDK by the local backend."""
    return _render(payload)


@app.function(image=health_image)
@modal.fastapi_endpoint(method="GET", label="health")
def health() -> dict[str, Any]:
    """Public, torch-free liveness probe (CPU only, a few milliseconds).

    It intentionally does *not* start a GPU container: an internet-reachable
    endpoint that spins up a T4 would be a standing cost risk.
    """
    return {
        "status": "ok",
        "app": APP_NAME,
        "function": FUNCTION_NAME,
        "gpu": GPU_TYPE,
        "sample_rate": SAMPLE_RATE,
    }


@app.local_entrypoint()
def main(text: str = "The quick brown fox jumps over the lazy dog.", voice: str = DEFAULT_VOICE) -> None:
    """Smoke-test the deployed function: ``modal run backend/modal_kokoro.py``."""
    result = synthesize.remote({"text": text, "voice": voice, "speed": 1.0})
    total_samples = sum(chunk["samples"] for chunk in result["chunks"])
    print(
        f"{result['device']} ({result['gpu']}): {len(result['chunks'])} chunk(s), "
        f"{total_samples} samples = {total_samples / result['sample_rate']:.2f}s, "
        f"{result['elapsed_ms']} ms"
    )
