"""Tests for the chapter-parallel Modal export path.

The Modal client is real; only its transport is faked. That keeps the decode,
the batching and the reassembly under test without a network, a GPU or
credentials.
"""
import base64
import io
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import db.database as _db
from db.models import Book, MP3Export, Sentence
from routers import mp3 as mp3_router
from services import engine_manager, modal_remote
from services.tts_engine import TTSEngine


def flac_b64(samples: np.ndarray) -> str:
    """A batch payload exactly as ``synthesize_batch`` would return it."""
    buffer = io.BytesIO()
    sf.write(buffer, np.asarray(samples, dtype=np.float32), 24000, format="FLAC", subtype="PCM_16")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def batch_result(batch_index: int, sentence_samples: list[int], value: float = 0.5) -> dict:
    audio = np.full(sum(sentence_samples), value, dtype=np.float32)
    return {
        "batch_index": batch_index,
        "sample_rate": 24000,
        "samples": int(audio.size),
        "sentence_samples": sentence_samples,
        "flac_b64": flac_b64(audio),
    }


def make_client(mapper) -> modal_remote.ModalKokoroClient:
    """A real client whose batch run is driven by ``mapper``."""
    client = modal_remote.ModalKokoroClient(
        config=modal_remote.RemoteConfig(transport="sdk"), invoke=lambda payload: {}
    )
    client.batch_mapper = mapper
    return client


class TestClientBatchCalls:
    def test_a_batch_is_decoded_with_its_sentence_lengths(self):
        client = make_client(lambda payloads: [batch_result(0, [2400, 1200])])
        batches = list(client.synthesize_batches([{"batch_index": 0, "sentences": []}]))
        assert len(batches) == 1
        assert batches[0].batch_index == 0
        assert batches[0].audio.size == 3600
        assert batches[0].audio.dtype == np.float32
        assert batches[0].sentence_samples == [2400, 1200]

    def test_the_payload_names_the_export_function_voice_and_speed(self):
        seen = {}

        def mapper(payloads):
            seen["payloads"] = payloads
            return [batch_result(p["batch_index"], [100]) for p in payloads]

        client = make_client(mapper)
        list(
            client.synthesize_batches(
                [{"batch_index": 0, "sentences": [{"index": 0, "text": "Hi."}]}],
                voice="am_michael",
                speed=1.5,
            )
        )
        assert seen["payloads"] == [
            {
                "batch_index": 0,
                "sentences": [{"index": 0, "text": "Hi."}],
                "voice": "am_michael",
                "speed": 1.5,
                "lang_code": "a",
            }
        ]

    def test_batches_arrive_in_completion_order(self):
        client = make_client(
            lambda payloads: [batch_result(2, [10]), batch_result(0, [10]), batch_result(1, [10])]
        )
        batches = list(
            client.synthesize_batches([{"batch_index": i, "sentences": []} for i in range(3)])
        )
        assert [b.batch_index for b in batches] == [2, 0, 1]

    def test_no_batches_means_no_call(self):
        def mapper(payloads):  # pragma: no cover - must not run
            raise AssertionError("nothing to send")

        assert list(make_client(mapper).synthesize_batches([])) == []

    @pytest.mark.parametrize(
        "result,fragment",
        [
            ({"sample_rate": 24000, "flac_b64": "x"}, "missing batch_index"),
            ({"batch_index": 0, "sample_rate": 8000, "flac_b64": "x"}, "sample_rate"),
            ({"batch_index": 3, "sample_rate": 24000}, "missing flac_b64"),
            ({"batch_index": 1, "sample_rate": 24000, "flac_b64": "bm90IGZsYWM="}, "not decodable FLAC"),
        ],
    )
    def test_bad_batch_payloads_are_rejected(self, result, fragment):
        client = make_client(lambda payloads: [result])
        with pytest.raises(modal_remote.RemoteSynthesisError, match=fragment):
            list(client.synthesize_batches([{"batch_index": 0, "sentences": []}]))

    def test_a_transport_failure_is_wrapped(self):
        def mapper(payloads):
            raise RuntimeError("container died")

        client = make_client(mapper)
        with pytest.raises(modal_remote.RemoteSynthesisError, match="container died"):
            list(client.synthesize_batches([{"batch_index": 0, "sentences": []}]))

    def test_the_sdk_mapper_is_required_without_an_injection(self):
        """A client with an injected transport has no Modal SDK to fan out with."""
        client = modal_remote.ModalKokoroClient(
            config=modal_remote.RemoteConfig(transport="sdk"), invoke=lambda payload: {}
        )
        client.batch_mapper = None
        with pytest.raises(modal_remote.RemoteSynthesisError, match="requires the Modal SDK transport"):
            client._default_batch_mapper()


@pytest.fixture
def export_env(tmp_path, monkeypatch):
    """A two-chapter book, an in-memory database and a redirected export dir."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(_db, "engine", engine)
    monkeypatch.setattr(mp3_router, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(mp3_router, "EXPORT_DIR_NAME", "", raising=False)

    with Session(engine) as session:
        session.add(
            Book(
                id="bk",
                title="Test Book",
                author="Test Author",
                file_path="/nonexistent.pdf",
                file_type="pdf",
                page_count=1,
                created_at=datetime.now(timezone.utc),
            )
        )
        # Chapter 0: two sentences. Chapter 1: one sentence. One filtered row.
        for index, (chapter, text, filtered) in enumerate(
            [
                (0, "First sentence.", False),
                (0, "Second sentence.", False),
                (1, "Third sentence.", False),
                (1, "Filtered out.", True),
            ]
        ):
            session.add(
                Sentence(
                    book_id="bk", index=index, text=text, page=0, x0=0.0, y0=0.0, x1=1.0, y1=1.0,
                    filtered=filtered, chapter=chapter, chapter_title=f"Chapter {chapter}",
                )
            )
        export = MP3Export(
            book_id="bk", voice="af_heart", speed=1.0, status="pending", progress=0,
            # The same options every test below renders with, because
            # `create_export` is what normally records them: a row that disagreed
            # with the options would let the export label its file wrongly with
            # nothing in the suite to notice.
            format="wav", bitrate_kbps=None,
            created_at=datetime.now(timezone.utc),
        )
        session.add(export)
        session.commit()
        session.refresh(export)
        export_id = export.id
    return engine, export_id


def wav_options(**overrides):
    """Export options for a format that needs no external encoder.

    These tests are about the *batching* path -- how a book is split, dispatched
    and reassembled -- not about the container, and ``conftest._hermetic_ffmpeg``
    pins ``ffmpeg_available`` off so that a verdict here cannot depend on whether
    the host happens to have ffmpeg installed. WAV is written by soundfile, so
    the export really completes and really produces a file on any machine.
    """
    return mp3_router.ExportOptions(format="wav", bitrate_kbps=None, **overrides)


def install_modal_engine(mapper, monkeypatch) -> modal_remote.ModalKokoroClient:
    client = make_client(mapper)
    monkeypatch.setattr(mp3_router, "_engine", TTSEngine(client))
    return client


def read_export(engine, export_id: int) -> MP3Export:
    with Session(engine) as session:
        return session.get(MP3Export, export_id)


class TestModalExportPath:
    def test_two_chapters_become_two_batches(self, export_env, monkeypatch):
        engine, export_id = export_env
        seen = []

        def mapper(payloads):
            seen.append([p["batch_index"] for p in payloads])
            return [batch_result(p["batch_index"], [2400] * len(p["sentences"])) for p in payloads]

        install_modal_engine(mapper, monkeypatch)
        mp3_router._run_export_blocking(export_id, "bk", "af_heart", 1.0, wav_options())

        assert seen == [[0, 1]]
        row = read_export(engine, export_id)
        assert row.status == "done", row.error_message
        assert row.batches_total == 2
        assert row.batches_done == 2
        assert row.phase == "complete"

    def test_out_of_order_completion_is_reassembled_in_sentence_order(self, export_env, monkeypatch):
        """Batches that arrive out of order must be concatenated by *index*.

        The two batches carry distinguishable audio for exactly this reason, and
        the file is decoded to check it: asserting ``status == "done"`` and a
        non-zero size, as this test used to, passes just as happily when the
        export concatenates in *arrival* order -- which is the bug it is named
        after. Chapter 1 arrives first (0.9) and chapter 0 second (0.1, 0.1); the
        written track must read 0.1, 0.1, 0.9.
        """
        import soundfile as sf

        engine, export_id = export_env

        def mapper(payloads):
            # Chapter 1 first, with distinguishable audio, then chapter 0.
            return [batch_result(1, [2400], value=0.9), batch_result(0, [2400, 2400], value=0.1)]

        install_modal_engine(mapper, monkeypatch)
        mp3_router._run_export_blocking(
            export_id, "bk", "af_heart", 1.0, wav_options(chapters=False)
        )

        row = read_export(engine, export_id)
        assert row.status == "done", row.error_message
        assert row.file_size and row.file_size > 0

        audio, rate = sf.read(str(row.file_path), dtype="float32")
        assert rate == 24000
        assert audio.size == 3 * 2400, (
            f"expected three sentence buffers in the track, got {audio.size} samples"
        )
        per_sentence = [
            float(audio[start:start + 2400].mean()) for start in (0, 2400, 4800)
        ]
        assert per_sentence == pytest.approx([0.1, 0.1, 0.9], abs=1e-3), (
            "the track is not in sentence order: chapter 1 (0.9) arrived first and "
            f"was written first, giving means {per_sentence}"
        )

    def test_progress_counts_finished_batches(self, export_env, monkeypatch):
        engine, export_id = export_env
        progress = []

        def mapper(payloads):
            for payload in payloads:
                progress.append(("sent", payload["batch_index"]))
                yield batch_result(payload["batch_index"], [2400] * len(payload["sentences"]))

        install_modal_engine(mapper, monkeypatch)
        mp3_router._run_export_blocking(export_id, "bk", "af_heart", 1.0, wav_options())
        assert progress == [("sent", 0), ("sent", 1)]
        assert read_export(engine, export_id).progress == 100

    def test_a_failed_batch_is_retried_once(self, export_env, monkeypatch):
        engine, export_id = export_env
        attempts = {"n": 0}

        def mapper(payloads):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("preempted container")
            return [batch_result(p["batch_index"], [2400] * len(p["sentences"])) for p in payloads]

        client = install_modal_engine(mapper, monkeypatch)
        # The first run raises, the router re-runs the missing batches.
        mp3_router._run_export_blocking(export_id, "bk", "af_heart", 1.0, wav_options())

        row = read_export(engine, export_id)
        assert attempts["n"] >= 2, "the batch run was not retried"
        assert row.status == "done", row.error_message

    def test_a_second_failure_names_the_phase_and_the_batch(self, export_env, monkeypatch):
        engine, export_id = export_env

        def mapper(payloads):
            raise RuntimeError("gpu fell over")

        install_modal_engine(mapper, monkeypatch)
        mp3_router._run_export_blocking(export_id, "bk", "af_heart", 1.0, wav_options())

        row = read_export(engine, export_id)
        assert row.status == "error"
        assert row.error_message.startswith("Failed while ")
        assert "gpu fell over" in row.error_message

    def test_a_local_engine_never_uses_the_batch_path(self, export_env, monkeypatch):
        engine, export_id = export_env
        calls = []

        def kokoro(text, voice=None, speed=None):
            calls.append(text)
            return [(None, None, np.full(2400, 0.2, dtype=np.float32))]

        monkeypatch.setattr(mp3_router, "_engine", TTSEngine(kokoro))
        mp3_router._run_export_blocking(export_id, "bk", "af_heart", 1.0, wav_options())

        row = read_export(engine, export_id)
        assert row.status == "done", row.error_message
        # Three unfiltered sentences, synthesised one at a time.
        assert len(calls) == 3
        assert row.batches_total == 0
        # The row and the file it points at must agree about what was written.
        assert row.format == "wav"
        assert Path(row.file_path).suffix == f".{row.format}"

    def test_a_local_export_never_claims_to_be_starting_a_gpu(self, export_env, monkeypatch):
        engine, export_id = export_env

        def kokoro(text, voice=None, speed=None):
            return [(None, None, np.full(2400, 0.2, dtype=np.float32))]

        monkeypatch.setattr(mp3_router, "_engine", TTSEngine(kokoro))
        assert mp3_router._initial_phase() == engine_manager.PHASE_PROCESSING


class TestExportFormatsThroughTheRouter:
    def test_format_and_bitrate_are_recorded(self, export_env, monkeypatch):
        engine, export_id = export_env

        def kokoro(text, voice=None, speed=None):
            return [(None, None, np.full(2400, 0.2, dtype=np.float32))]

        monkeypatch.setattr(mp3_router, "_engine", TTSEngine(kokoro))
        options = wav_options()
        mp3_router._run_export_blocking(export_id, "bk", "af_heart", 1.0, options)

        row = read_export(engine, export_id)
        assert row.status == "done", row.error_message
        assert Path(row.file_path).suffix == ".wav"

    def test_chapter_markers_are_written_when_asked_for(self, export_env, monkeypatch):
        engine, export_id = export_env

        def kokoro(text, voice=None, speed=None):
            return [(None, None, np.full(24000, 0.2, dtype=np.float32))]

        monkeypatch.setattr(mp3_router, "_engine", TTSEngine(kokoro))
        seen = {}
        original = mp3_router.export_encoding.encode

        def spy(audio, path, **kwargs):
            seen["metadata"] = kwargs.get("metadata")
            return original(audio, path, **kwargs)

        monkeypatch.setattr(mp3_router.export_encoding, "encode", spy)
        mp3_router._run_export_blocking(export_id, "bk", "af_heart", 1.0, wav_options())

        assert "[CHAPTER]" in seen["metadata"]
        assert "title=Chapter 0" in seen["metadata"]
        assert "title=Chapter 1" in seen["metadata"]

    def test_chapters_can_be_turned_off(self, export_env, monkeypatch):
        engine, export_id = export_env

        def kokoro(text, voice=None, speed=None):
            return [(None, None, np.full(2400, 0.2, dtype=np.float32))]

        monkeypatch.setattr(mp3_router, "_engine", TTSEngine(kokoro))
        seen = {}
        original = mp3_router.export_encoding.encode

        def spy(audio, path, **kwargs):
            seen["metadata"] = kwargs.get("metadata")
            return original(audio, path, **kwargs)

        monkeypatch.setattr(mp3_router.export_encoding, "encode", spy)
        mp3_router._run_export_blocking(
            export_id, "bk", "af_heart", 1.0, wav_options(chapters=False)
        )
        assert "[CHAPTER]" not in (seen["metadata"] or "")

    def test_split_by_chapter_produces_a_zip(self, export_env, monkeypatch):
        engine, export_id = export_env

        def kokoro(text, voice=None, speed=None):
            return [(None, None, np.full(24000, 0.2, dtype=np.float32))]

        monkeypatch.setattr(mp3_router, "_engine", TTSEngine(kokoro))
        mp3_router._run_export_blocking(
            export_id, "bk", "af_heart", 1.0, wav_options(split_by_chapter=True)
        )

        row = read_export(engine, export_id)
        assert row.status == "done", row.error_message
        assert Path(row.file_path).suffix == ".zip"
        import zipfile

        with zipfile.ZipFile(row.file_path) as bundle:
            names = bundle.namelist()
        assert len(names) == 2
        assert names[0].startswith("01 - ")


class TestExportFormatsEndpoint:
    def test_the_dialog_gets_the_encoder_table(self, monkeypatch):
        from fastapi.testclient import TestClient

        import main
        from services import export_encoding

        # Pinned rather than read from the host: without this the "available"
        # flags below would be whatever `shutil.which` says on this machine.
        monkeypatch.setattr(export_encoding, "ffmpeg_available", lambda: True)
        body = TestClient(main.app).get("/mp3/formats").json()
        by_id = {entry["id"]: entry for entry in body}
        assert set(by_id) == {"mp3", "m4b", "opus", "wav"}
        assert by_id["opus"]["bitrates"] == [16, 24, 32]
        assert by_id["opus"]["default_bitrate"] == 24
        assert by_id["wav"]["default_bitrate"] is None
        assert by_id["wav"]["available"] is True
        assert by_id["mp3"]["content_type"] == "audio/mpeg"
        # With an encoder present every format is offered, and nothing carries a
        # "why not" reason.
        for entry in body:
            assert entry["available"] is True, entry
            assert entry["reason"] is None, entry

    def test_m4b_is_unavailable_without_ffmpeg(self, monkeypatch):
        from fastapi.testclient import TestClient

        import main
        from services import export_encoding

        monkeypatch.setattr(export_encoding, "ffmpeg_available", lambda: False)
        body = TestClient(main.app).get("/mp3/formats").json()
        by_id = {entry["id"]: entry for entry in body}
        assert by_id["m4b"]["available"] is False
        assert "ffmpeg" in by_id["m4b"]["reason"]
        assert by_id["wav"]["available"] is True
        assert by_id["mp3"]["available"] is False
