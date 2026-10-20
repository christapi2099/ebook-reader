"""scripts/slim_manifest_check.py: exhaustive slim == schema 1 check, read-only."""
import hashlib
import json

from scripts import slim_manifest_check as smc
from services import render_manifest as rm
from tests.test_render_manifest import _v1


def _write(tmp_path, m):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(m))
    return p


def test_ok_on_a_schema1_render_and_file_untouched(tmp_path, capsys):
    p = _write(tmp_path, _v1())
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    assert smc.main([str(p)]) == 0
    out = capsys.readouterr().out
    assert "8 tiers" in out and "OK" in out and "slim:" in out
    assert hashlib.sha256(p.read_bytes()).hexdigest() == digest


def test_json_report_counts_every_word(tmp_path, capsys):
    v1 = _v1()
    p = _write(tmp_path, v1)
    assert smc.main([str(p), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    words = sum(len(s["words"]) for t in v1["tiers"].values() for s in t["segments"])
    assert rep["check"]["words"] == words and rep["check"]["ok"]
    assert rep["slim_bytes"] < rep["source_bytes"] and rep["slim_gzip_bytes"] < rep["slim_bytes"]


def test_compare_flags_a_wrong_slim():
    v1 = _v1()
    slim = rm.slim_manifest(v1)
    slim["tiers"]["1.5"]["actual_factor"] = 1.4
    rep = smc.compare(v1, slim)
    assert not rep["ok"] and rep["mismatch_count"] > 0


def test_schema2_source_checks_against_itself(tmp_path):
    p = _write(tmp_path, rm.slim_manifest(_v1()))
    assert smc.main([str(p)]) == 0


def test_missing_file_is_exit_2(tmp_path):
    assert smc.main([str(tmp_path / "nope.json")]) == 2
