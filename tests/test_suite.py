import json

import pytest

from halflife import __version__
from halflife.cli import main
from halflife.suite import SCHEMA, SUITES, SuiteSpec, leaderboard, load_results, run_suite, validate
from halflife.experiment import ExperimentConfig

TINY = SuiteSpec("v0-live", SUITES["v0-live"].payloads, SUITES["v0-live"].channels,
                 ExperimentConfig(trials=3, cycles=SUITES["v0-live"].base.cycles))


@pytest.fixture(scope="module")
def result():
    return run_suite(TINY, "test-host", defense="attributed", notes="unit test")


def test_result_shape_and_metadata(result):
    assert result["schema"] == SCHEMA and result["suite"] == "v0-live"
    assert result["halflife_version"] == __version__
    assert result["host"] == {"name": "test-host", "defense": "attributed",
                              "components": result["host"]["components"], "notes": "unit test"}
    assert len(result["entries"]) == len(TINY.payloads) * len(TINY.channels)
    assert 0 <= result["scores"]["persistence"] <= 1 and 0 <= result["scores"]["utility"] <= 1
    assert validate(result) == []
    json.dumps(result, allow_nan=False)


def test_validate_catches_problems(result):
    assert validate({}) and "schema" in validate({})[0]
    bad = dict(result, suite="nope")
    assert any("unknown suite" in e for e in validate(bad))
    missing = dict(result, entries=result["entries"][1:])
    assert any("cover exactly" in e for e in validate(missing))
    out_of_range = dict(result, scores={"persistence": 1.5, "utility": 0.2})
    assert any("persistence" in e for e in validate(out_of_range))
    short = json.loads(json.dumps(result))
    short["entries"][0]["detectors"]["behavioral"]["curve"] = [1.0]
    assert any("curve" in e for e in validate(short))
    nameless = dict(result, host={"name": ""})
    assert any("host.name" in e for e in validate(nameless))


def test_leaderboard_ranks_by_persistence_and_separates_suites(result):
    worse = json.loads(json.dumps(result))
    worse["host"]["name"] = "leaky"
    worse["scores"]["persistence"] = min(1.0, result["scores"]["persistence"] + 0.5)
    other = json.loads(json.dumps(result))
    other["suite"] = "v0"
    md = leaderboard([worse, result, other])
    assert md.index("## Suite v0\n") < md.index("## Suite v0-live")
    live = md.split("## Suite v0-live")[1]
    assert live.index("test-host") < live.index("leaky")
    assert leaderboard([]) == "No results."


def test_load_results_rejects_invalid(tmp_path, result):
    good, bad = tmp_path / "g.json", tmp_path / "b.json"
    good.write_text(json.dumps(result))
    bad.write_text(json.dumps({"schema": "x"}))
    assert len(load_results([good])) == 1
    with pytest.raises(ValueError, match="b.json"):
        load_results([good, bad])


def test_checkpoint_dir_resumes(tmp_path):
    a = run_suite(TINY, "h", checkpoint_dir=tmp_path)
    assert len(list(tmp_path.glob("*.jsonl"))) == len(TINY.payloads)
    b = run_suite(TINY, "h", checkpoint_dir=tmp_path)
    assert a["entries"] == b["entries"]


def test_committed_baselines_are_valid():
    from pathlib import Path
    files = sorted(Path(__file__).resolve().parent.parent.glob("results/*.json"))
    assert files, "baseline results should be committed"
    for d in load_results(files):
        assert d["suite"] == "v0" and d["host"]["name"].startswith("sim-")


def test_cli_suite_run_validate_leaderboard(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(SUITES, "v0-live", TINY)
    out = tmp_path / "r.json"
    assert main(["suite", "run", "--suite", "v0-live", "--host", "cli-host", "--out", str(out)]) == 0
    assert main(["suite", "validate", str(out)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert main(["suite", "validate", str(bad)]) == 1
    md = tmp_path / "LB.md"
    assert main(["suite", "leaderboard", str(out), "--out", str(md)]) == 0
    assert "cli-host" in md.read_text()
    assert main(["suite", "run", "--suite", "nope", "--host", "x", "--out", str(out)]) == 2


def test_cli_suite_live_estimates_without_spending(capsys, monkeypatch):
    import halflife.cli as cli
    monkeypatch.setattr(cli, "_make_client", lambda **kw: pytest.fail("no client without --yes"))
    assert main(["suite", "run", "--suite", "v0-live", "--host", "x", "--out", "/dev/null",
                 "--consolidator", "claude"]) == 0
    assert "Nothing was spent" in capsys.readouterr().out


def test_version_flag(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out
