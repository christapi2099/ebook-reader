from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from db.database import create_engine_and_tables
from routers import documents, library
from routers import tts as tts_router


def _init_kokoro():
    try:
        from kokoro import KPipeline
        pipeline = KPipeline(lang_code="a")
        return pipeline
    except Exception as e:
        print(f"[warn] Kokoro not available: {e}")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_engine_and_tables()
    Path("uploads").mkdir(exist_ok=True)
    kokoro = _init_kokoro()
    tts_router.set_kokoro(kokoro)
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(library.router)
app.include_router(tts_router.router)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/health")
async def health():
    return {"status": "ok"}
