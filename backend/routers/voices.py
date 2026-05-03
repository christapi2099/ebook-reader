import shutil
from pathlib import Path

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

VOICES_DIR = Path("voices")

ENGLISH_VOICE_CATALOG = [
    # American English
    {"id": "af_heart",   "name": "Heart",   "lang": "en-US", "gender": "female", "quality": "A",  "built_in": True},
    {"id": "af_bella",   "name": "Bella",   "lang": "en-US", "gender": "female", "quality": "A-", "built_in": True},
    {"id": "af_nicole",  "name": "Nicole",  "lang": "en-US", "gender": "female", "quality": "B-", "built_in": True},
    {"id": "af_nova",    "name": "Nova",    "lang": "en-US", "gender": "female", "quality": "C",  "built_in": True},
    {"id": "af_sky",     "name": "Sky",     "lang": "en-US", "gender": "female", "quality": "C-", "built_in": True},
    {"id": "af_sarah",   "name": "Sarah",   "lang": "en-US", "gender": "female", "quality": "C+", "built_in": True},
    {"id": "af_alloy",   "name": "Alloy",   "lang": "en-US", "gender": "female", "quality": "C",  "built_in": True},
    {"id": "af_aoede",   "name": "Aoede",   "lang": "en-US", "gender": "female", "quality": "C+", "built_in": True},
    {"id": "af_kore",    "name": "Kore",    "lang": "en-US", "gender": "female", "quality": "C+", "built_in": True},
    {"id": "af_jessica", "name": "Jessica", "lang": "en-US", "gender": "female", "quality": "D",  "built_in": True},
    {"id": "af_river",   "name": "River",   "lang": "en-US", "gender": "female", "quality": "D",  "built_in": True},
    {"id": "am_adam",    "name": "Adam",    "lang": "en-US", "gender": "male",   "quality": "F+", "built_in": True},
    {"id": "am_michael", "name": "Michael", "lang": "en-US", "gender": "male",   "quality": "C+", "built_in": True},
    {"id": "am_fenrir",  "name": "Fenrir",  "lang": "en-US", "gender": "male",   "quality": "C+", "built_in": True},
    {"id": "am_puck",    "name": "Puck",    "lang": "en-US", "gender": "male",   "quality": "C+", "built_in": True},
    {"id": "am_onyx",    "name": "Onyx",    "lang": "en-US", "gender": "male",   "quality": "D",  "built_in": True},
    {"id": "am_echo",    "name": "Echo",    "lang": "en-US", "gender": "male",   "quality": "D",  "built_in": True},
    {"id": "am_eric",    "name": "Eric",    "lang": "en-US", "gender": "male",   "quality": "D",  "built_in": True},
    {"id": "am_liam",    "name": "Liam",    "lang": "en-US", "gender": "male",   "quality": "D",  "built_in": True},
    {"id": "am_santa",   "name": "Santa",   "lang": "en-US", "gender": "male",   "quality": "D-", "built_in": True},
    # British English
    {"id": "bf_emma",     "name": "Emma",     "lang": "en-GB", "gender": "female", "quality": "B-", "built_in": True},
    {"id": "bf_isabella", "name": "Isabella", "lang": "en-GB", "gender": "female", "quality": "C",  "built_in": True},
    {"id": "bf_alice",    "name": "Alice",    "lang": "en-GB", "gender": "female", "quality": "D",  "built_in": True},
    {"id": "bf_lily",     "name": "Lily",     "lang": "en-GB", "gender": "female", "quality": "D",  "built_in": True},
    {"id": "bm_george",   "name": "George",   "lang": "en-GB", "gender": "male",   "quality": "C",  "built_in": True},
    {"id": "bm_lewis",    "name": "Lewis",    "lang": "en-GB", "gender": "male",   "quality": "D+", "built_in": True},
    {"id": "bm_fable",    "name": "Fable",    "lang": "en-GB", "gender": "male",   "quality": "C",  "built_in": True},
    {"id": "bm_daniel",   "name": "Daniel",   "lang": "en-GB", "gender": "male",   "quality": "D",  "built_in": True},
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
    custom = _scan_custom_voices()
    return ENGLISH_VOICE_CATALOG + custom


@router.post("/upload")
async def upload_voice(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".pt"):
        raise HTTPException(status_code=400, detail="Only .pt voice pack files are accepted")
    VOICES_DIR.mkdir(exist_ok=True)
    safe_name = Path(file.filename).name
    dest = VOICES_DIR / safe_name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"id": f"custom:{safe_name.replace('.pt', '')}", "path": str(dest)}


@router.delete("/{voice_id:path}")
def delete_voice(voice_id: str):
    # Only custom voices can be deleted
    if not voice_id.startswith("custom:"):
        raise HTTPException(status_code=403, detail="Cannot delete built-in voice")
    name = voice_id.replace("custom:", "", 1)
    dest = VOICES_DIR / f"{name}.pt"
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
        name = voice_id.replace("custom:", "", 1)
        voice_path = VOICES_DIR / f"{name}.pt"
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
    sf.write(buf, full, 24000, format="WAV", subtype="PCM_16")
    return Response(content=buf.getvalue(), media_type="audio/wav")
