import json
import re

import pytest

from halflife.cli import main
from halflife.showcase import TRACE_TRIALS, build_data, render_html


@pytest.fixture(scope="module")
def data():
    return build_data(trials=2, cycles=3, seed=1)


def test_grid_is_complete(data):
    # 3 channels x 5 payloads x 5 defenses x write-back on/off
    assert len(data["runs"]) == 150
    run = data["runs"]["tool_result|zombie|attributed|1"]
    assert set(run) == {"literal", "semantic", "behavioral", "taint", "benign"}
    assert len(run["behavioral"]["c"]) == 4
    assert run["behavioral"]["hl"]["st"] in {"observed", "censored", "never_established"}


def test_judge_sweep_keys_match_page(data):
    levels = data["meta"]["judge_levels"]
    assert {f"{a:.1f}|{b:.1f}" for a in levels for b in levels} == set(data["judge"])
    perfect = data["judge"]["0.0|0.0"]
    assert perfect["se"] == 1.0 and perfect["sp"] == 1.0 and perfect["obs"] == perfect["true"]


def test_traces_use_string_pool(data):
    assert len(data["traces"]) == 75
    tr = data["traces"]["tool_result|plain|none"][0]
    assert len(tr["notes"]) == 4 and len(tr["hits"]["literal"]) == 4
    src, trust, text, q = tr["notes"][0][0]
    assert data["strings"][trust] == "untrusted" and "pay-verify" in data["strings"][text]
    assert all(len(v) == TRACE_TRIALS for v in data["traces"].values())


def test_data_is_json_safe(data):
    text = json.dumps(data, allow_nan=False)  # raises on NaN/inf
    assert "Infinity" not in text


def test_render_embeds_data_and_escapes_script_end(data):
    evil = dict(data, strings=data["strings"] + ["</script><script>alert(1)</script>"])
    html = render_html(evil)
    assert html.startswith("<!doctype html>") and "<title>HALFLIFE Lab</title>" in html
    assert "__HALFLIFE_DATA__" not in html
    blob = re.search(r'<script id="halflife-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert json.loads(blob)["strings"][-1].startswith("</script>")
    frag = render_html(data, standalone=False)
    assert not frag.lstrip().startswith("<!doctype")


def test_deterministic(data):
    assert build_data(trials=2, cycles=3, seed=1) == data


def test_parallel_matches_serial(data):
    assert build_data(trials=2, cycles=3, seed=1, workers=2) == data


def test_cli_showcase(tmp_path, capsys):
    out = tmp_path / "s.html"
    assert main(["showcase", "--out", str(out), "--trials", "1", "--cycles", "2"]) == 0
    assert out.stat().st_size > 10_000
    assert "150 configurations" in capsys.readouterr().out
