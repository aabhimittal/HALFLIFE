import json

from halflife.cli import main
from halflife.experiment import ExperimentConfig, run_experiment
from halflife.report import ascii_plot, matrix_table, sparkline, summary


def test_sparkline_and_plot_edges():
    assert sparkline([0, 0.5, 1]) == " ▄█"
    assert sparkline([]) == ""
    assert ascii_plot({}) == ""
    plot = ascii_plot({"literal": [1.0], "behavioral": [0.0, 1.0]}, height=4)
    assert "L=literal" in plot and "A=behavioral" in plot


def test_summary_and_table_render():
    r = run_experiment(ExperimentConfig(trials=3, cycles=3, judge_fpr=0.1))
    text = summary(r)
    assert "behavioral" in text and "corrected" in text
    assert "| plain |" in matrix_table([r], markdown=True)


def test_cli_list(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "zombie" in out and "attributed" in out and "ttl=3" in out


def test_cli_run_json(tmp_path, capsys):
    path = tmp_path / "r.json"
    assert main(["run", "--payload", "zombie", "--trials", "3", "--cycles", "3", "--no-plot",
                 "--set", "obey_prob=1", "--json", str(path)]) == 0
    d = json.loads(path.read_text())
    assert d["config"]["consolidator"] == {"obey_prob": 1.0}


def test_cli_compare_markdown_json(tmp_path, capsys):
    path = tmp_path / "m.json"
    assert main(["compare", "--payloads", "plain", "--defenses", "none,ttl", "--channels", "tool_result,document",
                 "--trials", "2", "--cycles", "2", "--markdown", "--json", str(path)]) == 0
    assert len(json.loads(path.read_text())) == 4
    assert "|---|" in capsys.readouterr().out


def test_cli_errors(capsys):
    assert main(["run", "--trials", "0", "--cycles", "1"]) == 2
    assert "trials" in capsys.readouterr().err
    assert main(["compare", "--payloads", "nope", "--trials", "1", "--cycles", "1"]) == 2
    import pytest
    with pytest.raises(SystemExit):
        main(["run", "--set", "garbage"])
