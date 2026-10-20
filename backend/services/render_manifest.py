"""The sync manifest's two on-disk shapes, and the slim one the reader is served.

Schema 1 (every render published before 2026-10-20) repeats the whole timeline in
every tier: ``tiers[k].segments`` is the 1x timeline with each start/end divided
by that tier's ``actual_factor`` (``render_artifacts.build_tiers._entry``). A 29 h
book carries 18.5k segments and 246k words eight times over: 358 MB on disk.

Schema 2 (slim) stores the 1x timeline once, in ``segments``, and each tier is
only its metadata (``requested_factor``, ``actual_factor``, ``max_drift_ms``,
``file``, ``duration_ms`` of its timeline). A tier's times are
``1x time / actual_factor``, the same division the encoder used, so the result is
bit-identical to schema 1 for every render built by that code.

A tier whose schema 1 timeline is NOT that division (an older or hand-edited
render) keeps its own ``segments`` in the slim form, so nothing is ever lost:
``tier_segments`` returns them instead of dividing.

Published generations are never rewritten: the slim form of an old render is
built on the fly and cached outside the generation directory (``routers.library``).
"""
from __future__ import annotations

import math

SCHEMA_V1 = 1
SCHEMA_SLIM = 2
BASE_TIER = "1.0"
# Two timelines agree when every start/end is within this many ms (1 µs).
TIME_TOL_MS = 1e-3

_TIER_META = ("requested_factor", "actual_factor", "max_drift_ms")


def is_slim(m: dict) -> bool:
    return m.get("schema") == SCHEMA_SLIM


def _clean_word(w: dict) -> dict:
    return {"start_ms": w["start_ms"], "end_ms": w["end_ms"], "box": w.get("box"),
            "timing_source": w.get("timing_source")}


def clean_segments(segments: list[dict]) -> list[dict]:
    """The manifest's segment shape: what ``write_manifest`` has always kept."""
    return [{"index": s["index"], "start_ms": s["start_ms"], "end_ms": s["end_ms"],
             "gap_ms": s["gap_ms"], "words": [_clean_word(w) for w in s.get("words") or []]}
            for s in segments]


def divide_segments(segments: list[dict], actual: float) -> list[dict]:
    """A tier's timeline from the 1x one: exactly ``build_tiers._entry``'s division."""
    return [{**s, "start_ms": s["start_ms"] / actual, "end_ms": s["end_ms"] / actual,
             "words": [{**w, "start_ms": w["start_ms"] / actual, "end_ms": w["end_ms"] / actual}
                       for w in s["words"]]}
            for s in segments]


def _close(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=0.0, abs_tol=TIME_TOL_MS)
    return a == b


def _derivable(base: list[dict], tier: list[dict], actual: float) -> bool:
    """True when ``tier`` is ``base / actual`` within TIME_TOL_MS, with the same
    indexes, gaps, boxes and timing sources."""
    if len(base) != len(tier):
        return False
    for b, t in zip(base, tier):
        if b.get("index") != t.get("index") or b.get("gap_ms") != t.get("gap_ms"):
            return False
        if not (_close(b["start_ms"] / actual, t.get("start_ms"))
                and _close(b["end_ms"] / actual, t.get("end_ms"))):
            return False
        bw, tw = b.get("words") or [], t.get("words") or []
        if len(bw) != len(tw):
            return False
        for x, y in zip(bw, tw):
            if x.get("box") != y.get("box") or x.get("timing_source") != y.get("timing_source"):
                return False
            if not (_close(x["start_ms"] / actual, y.get("start_ms"))
                    and _close(x["end_ms"] / actual, y.get("end_ms"))):
                return False
    return True


def _base_key(tiers: dict) -> str | None:
    """The tier holding the 1x timeline: "1.0" when it has one, else any tier at 1.0."""
    candidates = [BASE_TIER, *[k for k in tiers if k != BASE_TIER]]
    for k in candidates:
        t = tiers.get(k)
        if (isinstance(t, dict) and isinstance(t.get("segments"), list)
                and t.get("actual_factor") == 1.0):
            return k
    return None


def _tier_entry(key: str, t: dict, duration_ms) -> dict:
    out = {k: t[k] for k in _TIER_META if k in t}
    out["file"] = f"audio_{key}.opus"
    act = t.get("actual_factor")
    if isinstance(duration_ms, (int, float)) and isinstance(act, (int, float)) and act > 0:
        out["duration_ms"] = duration_ms / act
    return out


def slim_manifest(m: dict) -> dict:
    """Schema 1 -> schema 2. A schema 2 manifest is returned unchanged.

    Every tier is checked against ``1x / actual_factor``; one that differs keeps
    its own ``segments``. Keys this module does not know are carried over.
    """
    if is_slim(m):
        return m
    tiers = m.get("tiers") or {}
    out = {k: v for k, v in m.items() if k != "tiers"}
    out["schema"] = SCHEMA_SLIM
    out["source_schema"] = m.get("schema", SCHEMA_V1)
    bkey = _base_key(tiers)
    base = clean_segments(tiers[bkey]["segments"]) if bkey else []
    out["segments"] = base
    slim_tiers = {}
    for key, t in tiers.items():
        if not isinstance(t, dict):
            slim_tiers[key] = t
            continue
        entry = _tier_entry(key, t, m.get("duration_ms"))
        segs = t.get("segments")
        if isinstance(segs, list) and key != bkey:
            act = t.get("actual_factor")
            ok = (bkey is not None and isinstance(act, (int, float)) and act > 0
                  and _derivable(base, segs, float(act)))
            if not ok:
                entry["segments"] = clean_segments(segs)
        slim_tiers[key] = entry
    out["tiers"] = slim_tiers
    return out


def tier_segments(m: dict, key: str) -> list[dict]:
    """One tier's timeline, in schema 1's shape, from a manifest of either schema."""
    t = (m.get("tiers") or {}).get(key)
    if not isinstance(t, dict):
        raise KeyError(key)
    if not is_slim(m) or "segments" in t:
        return t.get("segments") or []
    act = t.get("actual_factor")
    if not isinstance(act, (int, float)) or act <= 0:
        raise ValueError(f"tier {key}: bad actual_factor {act!r}")
    return divide_segments(m.get("segments") or [], float(act))


def expand_v1(m: dict) -> dict:
    """The schema 1 view of a manifest of either schema (every tier with segments)."""
    if not is_slim(m):
        return m
    out = {k: v for k, v in m.items() if k not in ("segments", "tiers", "source_schema")}
    out["schema"] = SCHEMA_V1
    out["tiers"] = {}
    for key, t in (m.get("tiers") or {}).items():
        entry = {k: t[k] for k in _TIER_META if k in t}
        entry["segments"] = tier_segments(m, key)
        out["tiers"][key] = entry
    return out
