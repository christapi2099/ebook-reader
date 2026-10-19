import shutil
from pathlib import Path

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from services import engine_manager
from services.tts_engine import SAMPLE_RATE

VOICES_DIR = Path("voices")
VOICE_EXT = ".pt"


def _resolve_custom_voice_path(name: str) -> Path:
    """Map a custom-voice *name* to a file inside VOICES_DIR, or raise 400.

    The name reaches this function straight from the URL path
    (``/voices/{voice_id:path}``), so it is attacker-controlled. Only a bare
    filename with no directory component is accepted, and the containment check
    runs on the *resolved* path so that ``..`` and symlinks cannot escape
    VOICES_DIR.
    """
    if not name or name in {".", ".."} or "\x00" in name:
        raise HTTPException(status_code=400, detail="Invalid voice id")

    candidate = Path(name)
    if candidate.is_absolute() or len(candidate.parts) != 1 or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid voice id")
    if candidate.suffix not in ("", VOICE_EXT):
        raise HTTPException(status_code=400, detail="Invalid voice id")

    base = VOICES_DIR.resolve()
    resolved = (base / f"{candidate.stem}{VOICE_EXT}").resolve()
    if not resolved.is_relative_to(base):
        raise HTTPException(status_code=400, detail="Invalid voice id")
    return resolved


ENGLISH_VOICE_CATALOG = [
    # American English
    {"id": "af_heart",   "name": "Heart",   "lang": "en-US", "gender": "female", "quality": "A",  "built_in": True, "engine": "kokoro"},
    {"id": "af_bella",   "name": "Bella",   "lang": "en-US", "gender": "female", "quality": "A-", "built_in": True, "engine": "kokoro"},
    {"id": "af_nicole",  "name": "Nicole",  "lang": "en-US", "gender": "female", "quality": "B-", "built_in": True, "engine": "kokoro"},
    {"id": "af_nova",    "name": "Nova",    "lang": "en-US", "gender": "female", "quality": "C",  "built_in": True, "engine": "kokoro"},
    {"id": "af_sky",     "name": "Sky",     "lang": "en-US", "gender": "female", "quality": "C-", "built_in": True, "engine": "kokoro"},
    {"id": "af_sarah",   "name": "Sarah",   "lang": "en-US", "gender": "female", "quality": "C+", "built_in": True, "engine": "kokoro"},
    {"id": "af_alloy",   "name": "Alloy",   "lang": "en-US", "gender": "female", "quality": "C",  "built_in": True, "engine": "kokoro"},
    {"id": "af_aoede",   "name": "Aoede",   "lang": "en-US", "gender": "female", "quality": "C+", "built_in": True, "engine": "kokoro"},
    {"id": "af_kore",    "name": "Kore",    "lang": "en-US", "gender": "female", "quality": "C+", "built_in": True, "engine": "kokoro"},
    {"id": "af_jessica", "name": "Jessica", "lang": "en-US", "gender": "female", "quality": "D",  "built_in": True, "engine": "kokoro"},
    {"id": "af_river",   "name": "River",   "lang": "en-US", "gender": "female", "quality": "D",  "built_in": True, "engine": "kokoro"},
    {"id": "am_adam",    "name": "Adam",    "lang": "en-US", "gender": "male",   "quality": "F+", "built_in": True, "engine": "kokoro"},
    {"id": "am_michael", "name": "Michael", "lang": "en-US", "gender": "male",   "quality": "C+", "built_in": True, "engine": "kokoro"},
    {"id": "am_fenrir",  "name": "Fenrir",  "lang": "en-US", "gender": "male",   "quality": "C+", "built_in": True, "engine": "kokoro"},
    {"id": "am_puck",    "name": "Puck",    "lang": "en-US", "gender": "male",   "quality": "C+", "built_in": True, "engine": "kokoro"},
    {"id": "am_onyx",    "name": "Onyx",    "lang": "en-US", "gender": "male",   "quality": "D",  "built_in": True, "engine": "kokoro"},
    {"id": "am_echo",    "name": "Echo",    "lang": "en-US", "gender": "male",   "quality": "D",  "built_in": True, "engine": "kokoro"},
    {"id": "am_eric",    "name": "Eric",    "lang": "en-US", "gender": "male",   "quality": "D",  "built_in": True, "engine": "kokoro"},
    {"id": "am_liam",    "name": "Liam",    "lang": "en-US", "gender": "male",   "quality": "D",  "built_in": True, "engine": "kokoro"},
    {"id": "am_santa",   "name": "Santa",   "lang": "en-US", "gender": "male",   "quality": "D-", "built_in": True, "engine": "kokoro"},
    # British English
    {"id": "bf_emma",     "name": "Emma",     "lang": "en-GB", "gender": "female", "quality": "B-", "built_in": True, "engine": "kokoro"},
    {"id": "bf_isabella", "name": "Isabella", "lang": "en-GB", "gender": "female", "quality": "C",  "built_in": True, "engine": "kokoro"},
    {"id": "bf_alice",    "name": "Alice",    "lang": "en-GB", "gender": "female", "quality": "D",  "built_in": True, "engine": "kokoro"},
    {"id": "bf_lily",     "name": "Lily",     "lang": "en-GB", "gender": "female", "quality": "D",  "built_in": True, "engine": "kokoro"},
    {"id": "bm_george",   "name": "George",   "lang": "en-GB", "gender": "male",   "quality": "C",  "built_in": True, "engine": "kokoro"},
    {"id": "bm_lewis",    "name": "Lewis",    "lang": "en-GB", "gender": "male",   "quality": "D+", "built_in": True, "engine": "kokoro"},
    {"id": "bm_fable",    "name": "Fable",    "lang": "en-GB", "gender": "male",   "quality": "C",  "built_in": True, "engine": "kokoro"},
    {"id": "bm_daniel",   "name": "Daniel",   "lang": "en-GB", "gender": "male",   "quality": "D",  "built_in": True, "engine": "kokoro"},
]

PIPER_VOICES_DIR = Path("voices/piper")

PIPER_VOICE_CATALOG = [
    {"id": "piper:en_US-lessac-medium", "name": "Lessac", "lang": "en-US", "gender": "female", "quality": "medium", "built_in": True, "engine": "piper"},
    {"id": "piper:en_US-amy-medium",     "name": "Amy",    "lang": "en-US", "gender": "female", "quality": "medium", "built_in": True, "engine": "piper"},
    {"id": "piper:en_US-ryan-medium",    "name": "Ryan",   "lang": "en-US", "gender": "male",   "quality": "medium", "built_in": True, "engine": "piper"},
]


def _scan_piper_voices():
    """Only list Piper catalog entries whose .onnx model is actually present."""
    if not PIPER_VOICES_DIR.exists():
        return []
    available = {p.stem for p in PIPER_VOICES_DIR.glob("*.onnx")}
    return [
        v for v in PIPER_VOICE_CATALOG
        if v["id"].split(":", 1)[1] in available
    ]

router = APIRouter(prefix="/voices")

_kokoro = None


def set_kokoro(kokoro):
    global _kokoro
    _kokoro = kokoro


def _scan_custom_voices():
    """Scan voices/ directory for uploaded .pt files."""
    if not VOICES_DIR.exists():
        return []
    custom = []
    for f in sorted(VOICES_DIR.iterdir()):
        if f.suffix == ".pt":
            custom.append({
                "id": f"custom:{f.stem}",
                "name": f.stem.replace("_", " ").title(),
                "lang": "en-US",
                "gender": "unknown",
                "quality": "custom",
                "built_in": False,
            })
    return custom


@router.get("")
def list_voices():
    active = engine_manager.manager.active()
    if active == engine_manager.PIPER:
        return _scan_piper_voices()
    return ENGLISH_VOICE_CATALOG + _scan_custom_voices()


@router.post("/upload")
async def upload_voice(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".pt"):
        raise HTTPException(status_code=400, detail="Only .pt voice pack files are accepted")
    VOICES_DIR.mkdir(exist_ok=True)
    # Same validation as delete/preview: the id we return must round-trip.
    dest = _resolve_custom_voice_path(file.filename)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"id": f"custom:{dest.stem}", "path": str(dest)}


@router.delete("/{voice_id:path}")
def delete_voice(voice_id: str):
    # Only custom voices can be deleted
    if not voice_id.startswith("custom:"):
        raise HTTPException(status_code=403, detail="Cannot delete built-in voice")
    dest = _resolve_custom_voice_path(voice_id.replace("custom:", "", 1))
    if not dest.exists():
        raise HTTPException(status_code=404, detail="Voice not found")
    dest.unlink()
    return {"ok": True}


@router.get("/preview/{voice_id:path}")
async def preview_voice(voice_id: str):
    """
    Synthesize a short preview sentence with the given voice.
    Returns WAV audio bytes.
    """
    import io
    import soundfile as sf

    if _kokoro is None:
        raise HTTPException(status_code=503, detail="TTS engine not available")

    preview_text = "The quick brown fox jumps over the lazy dog."

    # Resolve custom voice
    voice = voice_id
    if voice_id.startswith("custom:"):
        voice_path = _resolve_custom_voice_path(voice_id.replace("custom:", "", 1))
        if not voice_path.exists():
            raise HTTPException(status_code=404, detail="Voice not found")
        voice = str(voice_path)

    try:
        results = _kokoro(preview_text, voice=voice, speed=1.0)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Synthesis failed: {e}")

    parts = []
    for result in results:
        audio = result[-1]
        parts.append(audio if isinstance(audio, np.ndarray) else np.array(audio))

    if not parts:
        raise HTTPException(status_code=500, detail="No audio generated")

    full = np.concatenate(parts)
    buf = io.BytesIO()
    sf.write(buf, full, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return Response(content=buf.getvalue(), media_type="audio/wav")
