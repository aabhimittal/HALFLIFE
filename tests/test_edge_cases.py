"""Edge cases found by reading the code paths; each test failed before its fix."""

import json
import re
import threading
import time
from pathlib import Path

import pytest

from halflife.consolidator import SimulatedConsolidator
from halflife.experiment import ExperimentConfig, load_checkpoint, run_experiment
from halflife.llm import LLMJudge, parse_notes
from halflife.memory import Trust
from halflife.payloads import CANARY_URL, PAYLOADS
from halflife.semantic import LLMSemantic
from halflife.showcase import build_data, render_html
from halflife.suite import SUITES, leaderboard, load_results, run_suite, validate
from tests.test_llm import fake  # noqa: E402  (fake Claude client)


# ------------------------------------------------------------- page embedding

def test_showcase_json_cannot_break_out_of_its_script_element():
    """'</script>' alone is not enough: '<!--' followed by '<script' changes how
    the HTML tokenizer reads the rest of a script element."""
    data = build_data(trials=1, cycles=1)
    data["strings"].append("<!--<script>alert(1)</script>")
    html = render_html(data)
    blob = re.search(r'<script id="halflife-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert "<" not in blob
    assert json.loads(blob)["strings"][-1] == "<!--<script>alert(1)</script>"


# --------------------------------------------------------------- LLM parsing

@pytest.mark.parametrize("reply", ["**COMPLIES**", "Complies.", "`COMPLIES`", "  complies - the reply routes..."])
def test_judge_accepts_formatted_verdicts(reply):
    llm, _ = fake([reply])
    j = LLMJudge(llm)
    assert j("x", PAYLOADS["plain"]) is True and j.unparsed == 0


@pytest.mark.parametrize("reply,expected", [("**PRESENT**", True), ("Absent.", False), ("_ABSENT_", False)])
def test_semantic_accepts_formatted_verdicts(reply, expected):
    from halflife.memory import MemoryItem, Provenance, Sentence
    llm, _ = fake([reply])
    det = LLMSemantic(llm)
    item = MemoryItem([Sentence("note", Provenance("x", Trust.USER))])
    assert det([item], "ref", frozenset()) is expected and det.unparsed == 0


def test_parse_notes_tag_with_dotted_source_is_stripped():
    notes, provs = parse_notes("- [src=web.page trust=user] The user is vegetarian.", attributed=True, cycle=1)
    assert notes == ["The user is vegetarian."]          # tag must not leak into the note text
    assert provs[0].source == "web.page" and provs[0].trust == Trust.USER


# ------------------------------------------------------------- checkpointing

def test_checkpoint_with_corrupt_header_gives_clear_error(tmp_path):
    ck = tmp_path / "run.jsonl"
    ck.write_text('{"halflife_checkp')                   # crashed while writing the header
    with pytest.raises(ValueError, match="not a HALFLIFE checkpoint"):
        load_checkpoint(ck, "abc")


def test_failing_trial_stops_pending_work_and_keeps_finished_trials(tmp_path):
    """One trial raising (e.g. an auth error) must not leave the other workers grinding on."""
    started, lock = [], threading.Lock()

    class Boom:
        def respond(self, items, topic, rng, defense):
            with lock:
                started.append(1)
                n = len(started)
            if n == 1:
                raise RuntimeError("401 invalid x-api-key")
            time.sleep(0.05)
            from halflife.agent import Response
            return Response("no link")

    cfg = ExperimentConfig(trials=40, cycles=2, interactions_per_cycle=0)
    with pytest.raises(RuntimeError, match="401"):
        run_experiment(cfg, agent=Boom(), workers=2, checkpoint=tmp_path / "ck.jsonl")
    # 40 trials x 3 probes each would be 120 calls; cancelled work must stop far short of that
    assert len(started) < 40


# ------------------------------------------------------------- configuration

@pytest.mark.parametrize("bad", [{"obey_prob": 1.5}, {"merge_prob": -0.1}, {"decay": 0.0}, {"decay": 1.2},
                                 {"eviction_noise": -1}, {"paraphrase_rate": 2}])
def test_consolidator_rejects_out_of_range_parameters(bad):
    with pytest.raises(ValueError):
        SimulatedConsolidator(**bad)
    with pytest.raises(ValueError):
        run_experiment(ExperimentConfig(trials=1, cycles=1, consolidator=bad))


# ------------------------------------------------------------------- suite

def _tiny_result(name="h"):
    from halflife.suite import SuiteSpec
    spec = SuiteSpec("v0-live", SUITES["v0-live"].payloads, SUITES["v0-live"].channels,
                     ExperimentConfig(trials=2, cycles=SUITES["v0-live"].base.cycles))
    return run_suite(spec, name)


def test_leaderboard_escapes_markdown_in_host_names():
    r = _tiny_result("evil | host\nwith newline")
    md = leaderboard([r])
    row = next(line for line in md.splitlines() if "evil" in line)
    def cells(line):
        return len(re.findall(r"(?<!\\)\|", line))        # unescaped pipes separate cells
    header = next(line for line in md.splitlines() if line.startswith("| #"))
    assert cells(row) == cells(header)
    assert "\\|" in row


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(entries=5),
    lambda d: d.update(entries=[5]),
    lambda d: d.update(scores=None),
    lambda d: d.update(host="x"),
    lambda d: d["entries"][0].update(detectors=None),
])
def test_validate_never_crashes_on_wrong_types(mutate):
    d = json.loads(json.dumps(_tiny_result()))
    mutate(d)
    problems = validate(d)
    assert problems and all(isinstance(p, str) for p in problems)


def test_load_results_names_the_file_on_bad_json(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="broken.json"):
        load_results([p])


# --------------------------------------------------------------- packaging

def test_package_version_matches_pyproject():
    from halflife import __version__
    text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    assert re.search(r'^version = "([^"]+)"', text, re.M).group(1) == __version__
    assert re.search(r'^name = "([^"]+)"', text, re.M).group(1) == "halflife-bench"


def test_canary_url_is_reserved_example_domain():
    # the attack target must never be a resolvable real domain
    assert re.match(r"https://[a-z0-9-]+\.example/", CANARY_URL)


# ------------------------------------------------------------------- CLI

def test_cli_set_non_numeric_names_the_parameter(capsys):
    from halflife.cli import main
    assert main(["run", "--set", "obey_prob=abc", "--trials", "1", "--cycles", "1"]) == 2
    assert "obey_prob" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["run", "--set", "=0.5"])


def test_live_ctrl_c_with_workers_exits_cleanly_and_keeps_progress(monkeypatch, tmp_path, capsys):
    import halflife.cli as cli
    from tests.test_live import FakeAPI
    from halflife.llm import ClaudeClient

    api = FakeAPI()
    real_create = api.create

    def create(**kw):
        if api.calls >= 25:
            raise KeyboardInterrupt
        return real_create(**kw)
    api.create = create
    monkeypatch.setattr(cli, "_make_client", lambda **kw: ClaudeClient(client=api, **kw))
    ck = tmp_path / "ck.jsonl"
    code = cli.main(["live", "--yes", "--trials", "6", "--cycles", "2", "--workers", "3",
                     "--cache", "", "--checkpoint", str(ck)])
    assert code == 130
    assert "Re-run the same command to resume" in capsys.readouterr().out
    assert ck.exists() and ck.read_text().startswith('{"halflife_checkpoint"')
