"""Tests for batch planning and for turning audio into a downloadable file.

Both modules are pure: planning is arithmetic over sentence rows, and everything
in the encoder except `encode()` itself is metadata, offsets and argument
building. The one test that needs ffmpeg skips when it is not installed.
"""
import base64
import shutil
import struct
import subprocess

import numpy as np
import pytest

from services import export_encoding
from services.export_batches import DEFAULT_MAX_SENTENCES, plan_batches


def rows(spec):
    """Build a ``{index: row}`` mapping: ``[(chapter, text, filtered), ...]``."""
    out = {}
    for index, item in enumerate(spec):
        chapter, text = item[0], item[1]
        filtered = item[2] if len(item) > 2 else False
        out[index] = {
            "text": text,
            "filtered": filtered,
            "chapter": chapter,
            "chapter_title": f"Chapter {chapter}",
        }
    return out


class TestPlanBatches:
    def test_one_chapter_becomes_one_batch(self):
        batches = plan_batches(rows([(0, "One."), (0, "Two."), (0, "Three.")]))
        assert len(batches) == 1
        assert batches[0].sentence_indices == [0, 1, 2]

    def test_chapters_are_never_mixed(self):
        batches = plan_batches(rows([(0, "A."), (0, "B."), (1, "C.")]))
        assert [b.chapter for b in batches] == [0, 1]
        assert [b.sentence_indices for b in batches] == [[0, 1], [2]]

    def test_batch_indices_are_sequential(self):
        batches = plan_batches(rows([(0, "A."), (1, "B."), (2, "C.")]))
        assert [b.batch_index for b in batches] == [0, 1, 2]

    def test_a_long_chapter_is_split(self):
        spec = [(0, f"Sentence {i}.") for i in range(7)]
        batches = plan_batches(rows(spec), max_sentences=3)
        assert [len(b.sentences) for b in batches] == [3, 3, 1]
        assert [b.chapter for b in batches] == [0, 0, 0]
        # Splitting must preserve order across the split boundaries.
        assert [i for b in batches for i in b.sentence_indices] == list(range(7))

    def test_filtered_sentences_are_dropped(self):
        batches = plan_batches(rows([(0, "A."), (0, "B.", True), (0, "C.")]))
        assert batches[0].sentence_indices == [0, 2]

    def test_blank_text_is_dropped(self):
        batches = plan_batches(rows([(0, "A."), (0, "   "), (0, "C.")]))
        assert batches[0].sentence_indices == [0, 2]

    def test_pasted_text_has_no_chapter(self):
        batches = plan_batches({0: {"text": "A.", "filtered": False}, 1: {"text": "B.", "filtered": False}})
        assert len(batches) == 1
        assert batches[0].chapter is None

    def test_a_chapter_whose_sentences_are_all_filtered_disappears(self):
        batches = plan_batches(rows([(0, "A."), (1, "B.", True), (2, "C.")]))
        assert [b.chapter for b in batches] == [0, 2]

    def test_an_empty_book_plans_no_batches(self):
        assert plan_batches({}) == []

    def test_only_filtered_sentences_plan_no_batches(self):
        assert plan_batches(rows([(0, "A.", True)])) == []

    def test_a_zero_limit_is_rejected(self):
        with pytest.raises(ValueError, match="at least 1"):
            plan_batches(rows([(0, "A.")]), max_sentences=0)

    def test_the_wire_payload_carries_index_and_text(self):
        batch = plan_batches(rows([(0, "Hello.")]))[0]
        assert batch.as_payload() == {"batch_index": 0, "sentences": [{"index": 0, "text": "Hello."}]}

    def test_the_default_limit_is_the_documented_one(self):
        assert DEFAULT_MAX_SENTENCES == 150


class TestMetadata:
    def test_values_are_escaped(self):
        assert export_encoding.escape_metadata_value("a;b#c=d\\e") == "a\\;b\\#c\\=d\\\\e"

    def test_newlines_become_spaces(self):
        assert export_encoding.escape_metadata_value("one\ntwo") == "one two"

    def test_title_and_artist_are_written(self):
        document = export_encoding.build_ffmetadata("My Book", "An Author")
        assert document.startswith(";FFMETADATA1")
        assert "title=My Book" in document
        assert "artist=An Author" in document

    def test_chapters_are_written_with_a_timebase(self):
        document = export_encoding.build_ffmetadata(
            "Book", "Author", [{"start_ms": 0, "end_ms": 1000, "title": "Opening"}]
        )
        assert "[CHAPTER]" in document
        assert "TIMEBASE=1/1000" in document
        assert "START=0" in document
        assert "END=1000" in document
        assert "title=Opening" in document

    def test_an_unnamed_chapter_is_numbered(self):
        document = export_encoding.build_ffmetadata(None, None, [{"start_ms": 0, "end_ms": 10}])
        assert "title=Chapter 1" in document

    def test_a_zero_length_chapter_is_skipped(self):
        document = export_encoding.build_ffmetadata(
            None, None, [{"start_ms": 500, "end_ms": 500, "title": "Empty"}]
        )
        assert "[CHAPTER]" not in document

    def test_chapter_numbers_do_not_depend_on_line_position(self):
        document = export_encoding.build_ffmetadata(
            "Book",
            "Author",
            [{"start_ms": 0, "end_ms": 10}, {"start_ms": 10, "end_ms": 20}],
        )
        assert "title=Chapter 1" in document
        assert "title=Chapter 2" in document


class TestChapterMarks:
    def test_marks_follow_the_audio_offsets(self):
        offsets = {0: (0, 24000), 1: (24000, 48000), 2: (48000, 72000)}
        marks = export_encoding.chapter_marks(
            offsets,
            {0: {"chapter": 0, "chapter_title": "A"}, 1: {"chapter": 0, "chapter_title": "A"},
             2: {"chapter": 1, "chapter_title": "B"}},
        )
        assert [(m["start_ms"], m["end_ms"]) for m in marks] == [(0, 2000), (2000, 3000)]
        assert [m["title"] for m in marks] == ["A", "B"]

    def test_a_single_unnamed_run_produces_no_markers(self):
        """One "Chapter 1" at 00:00 is noise, not a chapter list."""
        assert export_encoding.chapter_marks({0: (0, 24000)}, {0: {"chapter": 0}}) == []

    def test_one_named_chapter_is_kept(self):
        marks = export_encoding.chapter_marks(
            {0: (0, 24000)}, {0: {"chapter": 0, "chapter_title": "Prologue"}}
        )
        assert [m["title"] for m in marks] == ["Prologue"]

    def test_sentences_without_offsets_cannot_contribute(self):
        marks = export_encoding.chapter_marks(
            {2: (0, 24000)},
            {0: {"chapter": 0, "chapter_title": "A"}, 2: {"chapter": 1, "chapter_title": "B"}},
        )
        assert len(marks) == 1
        assert marks[0]["title"] == "B"


class TestFormatValidation:
    def test_defaults_are_the_researched_ones(self):
        assert export_encoding.normalise_format(None) == "mp3"
        assert export_encoding.normalise_bitrate("mp3", None) == 64
        assert export_encoding.normalise_bitrate("opus", None) == 24
        assert export_encoding.normalise_bitrate("m4b", None) == 64

    def test_an_unknown_format_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown format"):
            export_encoding.normalise_format("flac")

    @pytest.mark.parametrize(
        "fmt,allowed", [("mp3", [64, 128, 192]), ("opus", [16, 24, 32]), ("m4b", [64, 96, 128])]
    )
    def test_only_the_tested_bitrates_are_accepted(self, fmt, allowed):
        for value in allowed:
            assert export_encoding.normalise_bitrate(fmt, value) == value
        with pytest.raises(ValueError, match="must be one of"):
            export_encoding.normalise_bitrate(fmt, 9999)

    def test_wav_takes_no_bitrate(self):
        assert export_encoding.normalise_bitrate("wav", None) is None
        with pytest.raises(ValueError, match="no bitrate setting"):
            export_encoding.normalise_bitrate("wav", 64)

    def test_wav_needs_no_ffmpeg(self):
        assert export_encoding.FORMATS["wav"].needs_ffmpeg is False
        assert export_encoding.FORMATS["wav"].supports_metadata is False
        assert export_encoding.FORMATS["mp3"].needs_ffmpeg is True

    def test_content_types_match_the_container(self):
        assert export_encoding.FORMATS["mp3"].content_type == "audio/mpeg"
        assert export_encoding.FORMATS["m4b"].content_type == "audio/mp4"
        assert export_encoding.FORMATS["opus"].content_type == "audio/ogg"
        assert export_encoding.FORMATS["wav"].content_type == "audio/wav"


class TestFfmpegArgs:
    def test_mp3_sets_lame_and_an_explicit_bitrate(self):
        args = export_encoding.ffmpeg_args("mp3", 128)
        assert "libmp3lame" in args
        assert args[args.index("-b:a") + 1] == "128k"

    def test_mp3_tags_use_id3v23(self):
        args = export_encoding.ffmpeg_args("mp3", 64, has_metadata=True)
        # Read the flag's value, not the presence of the character "3" somewhere
        # in the argument list: `assert "3" in args` also passes for a bitrate of
        # "3k" or any unrelated argument that happens to be "3".
        assert args[args.index("-id3v2_version") + 1] == "3"

    def test_m4b_is_faststart_mp4_with_chapters(self):
        args = export_encoding.ffmpeg_args("m4b", 64, has_chapters=True)
        assert "aac" in args
        assert "+faststart" in args
        assert "-map_chapters" in args

    def test_opus_targets_ogg(self):
        args = export_encoding.ffmpeg_args("opus", 24)
        assert "libopus" in args
        assert "ogg" in args

    def test_wav_is_not_an_ffmpeg_job(self):
        with pytest.raises(ValueError, match="soundfile"):
            export_encoding.ffmpeg_args("wav", None)


class TestCoverBlock:
    def test_the_block_is_a_flac_picture_with_the_image_inside(self):
        image = b"\xff\xd8\xffJPEGDATA"
        block = export_encoding.flac_picture_block(image, width=10, height=20)
        assert block.endswith(image)
        # type(3), mime length, mime, description length, ...
        # >I type, >I mime length, mime, >I description length, description, ...
        assert struct.unpack(">I", block[:4])[0] == 3
        mime_len = struct.unpack(">I", block[4:8])[0]
        assert mime_len == len("image/jpeg")
        assert block[8:8 + mime_len] == b"image/jpeg"
        desc_offset = 8 + mime_len
        desc_len = struct.unpack(">I", block[desc_offset:desc_offset + 4])[0]
        assert desc_len == len("Cover")
        # The image length is declared immediately before the image itself.
        image_len_offset = len(block) - 4 - len(image)
        assert struct.unpack(">I", block[image_len_offset:image_len_offset + 4])[0] == len(image)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
class TestEncodingWithRealFfmpeg:
    """One real encode per format, verified by ffprobe.

    The only place in the suite that runs ffmpeg, and it says so twice: the
    ``skipif`` on this class is the single opt-out from the suite's rule that
    ffmpeg availability must not change a verdict, and the autouse ``real_ffmpeg``
    fixture below undoes ``conftest._hermetic_ffmpeg``'s pin so the encoder probe
    reports the truth here.

    Skipped when ffmpeg is absent, which is the documented packaging state for
    the .deb/Flatpak before their dependency lists are updated. Everything a
    reader needs to know *about* the formats without an encoder -- codec
    arguments, bitrate allow-lists, chapter marks, the cover block -- is asserted
    by the classes above, which never skip.
    """

    @pytest.fixture(autouse=True)
    def _needs_the_real_binary(self, real_ffmpeg):
        """Use the genuine ffmpeg probe, not the suite-wide pinned-off one."""

    @pytest.fixture
    def tone(self):
        sample_rate = export_encoding.SAMPLE_RATE
        t = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
        return (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    def _probe(self, path):
        """Everything ffprobe will say about the container, tags and duration.

        Tags are asserted against the whole document, not against
        ``format_tags``: MP4 and MP3 put them on the format, while Ogg/Opus
        keeps them on the stream, and the point of the assertion is that the
        tags are *in the file*.
        """
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries",
                "format=duration,format_name:format_tags:stream=codec_name:stream_tags",
                "-show_chapters", "-of", "default=noprint_wrappers=1", str(path),
            ],
            capture_output=True, text=True,
        )
        assert out.returncode == 0, out.stderr
        return out.stdout

    @pytest.mark.parametrize(
        "fmt,bitrate,codec,container",
        [
            ("mp3", 64, "mp3", "mp3"),
            ("m4b", 64, "aac", "mp4"),
            ("opus", 24, "opus", "ogg"),
        ],
    )
    def test_each_format_encodes_in_its_own_codec_and_reports_its_tags(
        self, tmp_path, tone, fmt, bitrate, codec, container
    ):
        """The file must really *be* the format that was asked for.

        Asserting only the tags, as this test used to, left the codec unchecked:
        an ``.opus`` file containing MP3 bytes (``ffmpeg_args("opus")`` returning
        ``-c:a libmp3lame ... -f mp3``) passed the whole ffmpeg-gated suite. The
        codec and the container are what the ``fmt`` argument is *for*, so they
        are asserted positively, per format.
        """
        target = tmp_path / f"out.{export_encoding.FORMATS[fmt].extension}"
        metadata = export_encoding.build_ffmetadata(
            "Test Title", "Test Author", [{"start_ms": 0, "end_ms": 1000, "title": "One"}]
        )
        export_encoding.encode(tone, target, fmt=fmt, bitrate_kbps=bitrate, metadata=metadata)
        assert target.exists() and target.stat().st_size > 0
        probed = self._probe(target)
        assert f"codec_name={codec}" in probed, (
            f"{fmt} was written with the wrong codec:\n{probed}"
        )
        assert container in next(
            line for line in probed.splitlines() if line.startswith("format_name=")
        ), f"{fmt} was written in the wrong container:\n{probed}"
        assert "Test Title" in probed
        assert "Test Author" in probed
        assert "duration=" in probed

    def test_mp3_with_a_cover_keeps_the_picture(self, tmp_path, tone):
        import pymupdf

        # A real JPEG, made the same way the export path makes one: render a
        # page. ffmpeg refuses anything that is not decodable image data.
        with pymupdf.open() as document:
            page = document.new_page()
            jpeg = page.get_pixmap(dpi=72).tobytes("jpeg")
        target = tmp_path / "covered.mp3"
        export_encoding.encode(
            tone,
            target,
            fmt="mp3",
            bitrate_kbps=64,
            metadata=export_encoding.build_ffmetadata("T", "A"),
            cover=jpeg,
        )
        assert target.exists() and target.stat().st_size > 0
        assert "video" in subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "default=noprint_wrappers=1", str(target)],
            capture_output=True, text=True,
        ).stdout

    def test_a_missing_encoder_is_reported_clearly(self, tmp_path, tone, monkeypatch):
        monkeypatch.setattr(export_encoding, "ffmpeg_available", lambda: False)
        with pytest.raises(export_encoding.EncodingError, match="ffmpeg not found"):
            export_encoding.encode(tone, tmp_path / "x.m4b", fmt="m4b", bitrate_kbps=64)


class TestEncodingWithoutFfmpeg:
    """The half of ``encode`` that must work on a machine without an encoder.

    This used to live inside ``TestEncodingWithRealFfmpeg``, so on a machine
    without ffmpeg the assertion that a WAV is really written -- and that the
    missing-encoder error is clear rather than a traceback -- was skipped along
    with the tests that genuinely need the binary. It never needs ffmpeg, so it
    no longer shares their ``skipif``.
    """

    @pytest.fixture
    def tone(self):
        sample_rate = export_encoding.SAMPLE_RATE
        t = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
        return (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    def test_wav_is_written_without_ffmpeg(self, tmp_path, tone):
        import soundfile as sf

        target = tmp_path / "out.wav"
        export_encoding.encode(tone, target, fmt="wav")

        assert target.exists() and target.stat().st_size > 0
        data, rate = sf.read(str(target), dtype="float32")
        assert rate == export_encoding.SAMPLE_RATE
        assert data.size == tone.size

    @pytest.mark.parametrize("fmt,bitrate", [("mp3", 64), ("m4b", 64), ("opus", 24)])
    def test_a_format_that_needs_ffmpeg_says_so(self, tmp_path, tone, fmt, bitrate):
        """``ffmpeg_available`` is pinned off for the whole suite by conftest."""
        with pytest.raises(export_encoding.EncodingError, match="ffmpeg not found"):
            export_encoding.encode(
                tone,
                tmp_path / f"out.{export_encoding.FORMATS[fmt].extension}",
                fmt=fmt,
                bitrate_kbps=bitrate,
            )
        assert not (tmp_path / f"out.{export_encoding.FORMATS[fmt].extension}").exists(), (
            "a failed encode must not leave a half-written file behind"
        )
