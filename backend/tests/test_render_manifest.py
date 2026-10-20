"""Slim manifest (schema 2): one 1x timeline, tiers as factors, equal to schema 1."""
import copy
import json
import random

import pytest

from services import render_manifest as rm

# The factors a real 29 h render measured (ffprobe of the atempo files), old tier set included.
ODD_FACTORS = {"1.0": 1.0, "0.75": 0.7500001515521114, "1.25": 1.2500001452431144,
               "1.5": 1.5000000248152556, "1.75": 1.7500001854154703, "2.0": 1.9999999874080308,
               "2.5": 2.5000009354763852, "3.0": 3.000000751547916}


def _base(n=40, seed=7):
    rnd = random.Random(seed)
    segs, pos = [], 0.0
    for i in range(n):
        dur = rnd.uniform(400, 6000)
        nw = rnd.randint(0, 9)
        words = []
        for k in range(nw):
            ws = pos + dur * k / max(nw, 1)
            words.append({"start_ms": ws, "end_ms": ws + dur / max(nw, 1) - rnd.uniform(0, 5),
                          "box": None if rnd.random() < 0.05 else
                          [rnd.uniform(0, 500), rnd.uniform(0, 700), rnd.uniform(0, 500), rnd.uniform(0, 700)],
                          "timing_source": rnd.choice(["aligned", "estimated", "kokoro"]),
                          "word": "dropped-by-the-writer"})
        gap = rnd.choice([0, 120, 250])
        segs.append({"index": i * 2 + (i % 3), "start_ms": pos, "end_ms": pos + dur,
                     "gap_ms": gap, "words": words})
        pos += dur + gap
    return segs


def _v1(factors=ODD_FACTORS, n=40):
    """A schema 1 manifest exactly as build_tiers + write_manifest produced it."""
    base = rm.clean_segments(_base(n))
    tiers = {k: {"requested_factor": float(k), "actual_factor": a, "max_drift_ms": 3.5,
                 "segments": rm.divide_segments(base, a)} for k, a in factors.items()}
    m = {"schema": 1, "duration_ms": base[-1]["end_ms"], "base_effective_speed": 1.0,
         "voice": "af_heart", "voice_file_sha256": "ab", "fingerprint": "fp",
         "container": "ogg-opus", "sample_rate": 24000, "timing_quality": "aligned",
         "mixed_backends": False, "gap_policy": "g", "tiers": tiers}
    return json.loads(json.dumps(m))  # what is on disk


def _assert_same_timeline(got, want):
    assert [s["index"] for s in got] == [s["index"] for s in want]
    for g, w in zip(got, want):
        assert g["gap_ms"] == w["gap_ms"]
        assert g["start_ms"] == pytest.approx(w["start_ms"], abs=1e-6)
        assert g["end_ms"] == pytest.approx(w["end_ms"], abs=1e-6)
        assert len(g["words"]) == len(w["words"])
        for a, b in zip(g["words"], w["words"]):
            assert a["box"] == b["box"] and a["timing_source"] == b["timing_source"]
            assert a["start_ms"] == pytest.approx(b["start_ms"], abs=1e-6)
            assert a["end_ms"] == pytest.approx(b["end_ms"], abs=1e-6)


def test_slim_has_one_timeline_and_factor_only_tiers():
    v1 = _v1()
    slim = rm.slim_manifest(v1)
    assert slim["schema"] == 2 and slim["source_schema"] == 1
    assert len(slim["segments"]) == 40
    assert set(slim["tiers"]) == set(ODD_FACTORS)
    for k, t in slim["tiers"].items():
        assert "segments" not in t, k
        assert t["actual_factor"] == ODD_FACTORS[k] and t["requested_factor"] == float(k)
        assert t["max_drift_ms"] == 3.5 and t["file"] == f"audio_{k}.opus"
        assert t["duration_ms"] == pytest.approx(v1["duration_ms"] / ODD_FACTORS[k])
    for key in ("voice", "fingerprint", "timing_quality", "gap_policy", "sample_rate", "duration_ms"):
        assert slim[key] == v1[key]


@pytest.mark.parametrize("key", list(ODD_FACTORS))
def test_every_tier_equals_v1_after_division(key):
    v1 = _v1()
    slim = json.loads(json.dumps(rm.slim_manifest(v1)))  # through the wire
    got = rm.tier_segments(slim, key)
    want = v1["tiers"][key]["segments"]
    _assert_same_timeline(got, want)
    assert got == want  # bit-identical: the same division the encoder did


def test_new_tier_set_and_slim_is_much_smaller():
    v1 = _v1({"1.0": 1.0, "1.5": 1.5000000248152556, "2.0": 1.9999999874080308,
              "2.5": 2.5000009354763852, "3.0": 3.000000751547916}, n=200)
    slim = rm.slim_manifest(v1)
    assert len(json.dumps(slim)) * 4 < len(json.dumps(v1))
    for k in v1["tiers"]:
        assert rm.tier_segments(slim, k) == v1["tiers"][k]["segments"]


def test_slim_is_idempotent_and_expands_back_to_v1():
    v1 = _v1()
    slim = rm.slim_manifest(v1)
    assert rm.slim_manifest(slim) is slim
    back = rm.expand_v1(slim)
    assert back["schema"] == 1 and "segments" not in back and "source_schema" not in back
    assert back == v1
    assert rm.expand_v1(v1) is v1


def test_a_tier_that_is_not_the_division_keeps_its_own_segments():
    v1 = _v1()
    v1["tiers"]["1.25"]["segments"][3]["words"][0]["start_ms"] += 7.0  # an odd old render
    v1["tiers"]["2.0"]["segments"][5]["start_ms"] += 0.01
    slim = rm.slim_manifest(v1)
    assert "segments" in slim["tiers"]["1.25"] and "segments" in slim["tiers"]["2.0"]
    assert "segments" not in slim["tiers"]["1.5"]
    for k in v1["tiers"]:
        _assert_same_timeline(rm.tier_segments(slim, k), v1["tiers"][k]["segments"])


def test_sub_microsecond_float_noise_still_derives():
    v1 = _v1()
    v1["tiers"]["1.5"]["segments"][0]["end_ms"] += 1e-7
    slim = rm.slim_manifest(v1)
    assert "segments" not in slim["tiers"]["1.5"]


def test_mismatched_boxes_or_counts_are_kept_verbatim():
    v1 = _v1()
    v1["tiers"]["3.0"]["segments"][2]["words"][0]["box"] = [1, 2, 3, 4]
    v1["tiers"]["0.75"]["segments"].pop()
    slim = rm.slim_manifest(v1)
    assert "segments" in slim["tiers"]["3.0"] and "segments" in slim["tiers"]["0.75"]
    assert rm.tier_segments(slim, "0.75") == v1["tiers"]["0.75"]["segments"]


def test_no_1x_tier_keeps_every_tier_whole():
    v1 = _v1()
    del v1["tiers"]["1.0"]
    slim = rm.slim_manifest(v1)
    assert slim["segments"] == []
    for k in v1["tiers"]:
        assert rm.tier_segments(slim, k) == v1["tiers"][k]["segments"]


def test_base_from_another_key_with_factor_one():
    v1 = _v1({"1": 1.0, "2.0": 1.9999999874080308})
    slim = rm.slim_manifest(v1)
    assert "segments" not in slim["tiers"]["2.0"]
    assert rm.tier_segments(slim, "2.0") == v1["tiers"]["2.0"]["segments"]


def test_tolerates_minimal_and_unknown_shapes():
    m = {"generation": 1, "tiers": {"1.0": {}}}
    slim = rm.slim_manifest(m)
    assert slim["generation"] == 1 and slim["segments"] == [] and "1.0" in slim["tiers"]
    assert rm.slim_manifest({})["tiers"] == {}


def test_tier_segments_errors():
    slim = rm.slim_manifest(_v1())
    with pytest.raises(KeyError):
        rm.tier_segments(slim, "9.0")
    bad = copy.deepcopy(slim)
    bad["tiers"]["1.5"]["actual_factor"] = 0
    with pytest.raises(ValueError):
        rm.tier_segments(bad, "1.5")
