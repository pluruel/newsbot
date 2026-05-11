# tests/test_apply_graph.py
import os
import pytest
from pathlib import Path
from unittest.mock import patch

import newsparser.scripts.apply_graph as script


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    (tmp_path / "workspace" / "cycles" / "tech").mkdir(parents=True)


SAMPLE_REPORT = """\
사이클 2026-05-08 12:00 KST

새 소식
• 없음

## Graph updates
### Entities
- NEW | Company | OpenAI | aliases: []

### Relations
- NEW | OpenAI --ANNOUNCED[conf:0.9, impact:0.8]--> GPT-5 | announced new model
"""


def test_apply_graph_calls_apply_graph_updates(tmp_path):
    ws = Path(os.environ["WORKSPACE_DIR"])
    (ws / "cycles" / "tech" / "2026-05-08-12.md").write_text(SAMPLE_REPORT)

    with patch("newsparser.scripts.apply_graph.apply_graph_updates") as mock_apply:
        script.main(["apply_graph.py", "tech", "2026-05-08-12"])

    mock_apply.assert_called_once()
    entities, relations = mock_apply.call_args.args
    assert any(e.name == "OpenAI" for e in entities)
    assert mock_apply.call_args.kwargs["cycle_id"] == "tech-2026-05-08-12"
    assert mock_apply.call_args.kwargs["category"] == "tech"


def test_apply_graph_exits_1_if_report_missing():
    with pytest.raises(SystemExit) as exc:
        script.main(["apply_graph.py", "tech", "9999-99-99-99"])
    assert exc.value.code == 1


def test_apply_graph_exits_1_on_wrong_args():
    with pytest.raises(SystemExit) as exc:
        script.main(["apply_graph.py", "tech"])
    assert exc.value.code == 1


def test_resolver_maps_A_indices_to_real_guids(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace"
    (ws / "input" / "markets").mkdir(parents=True)
    (ws / "input" / "markets" / "2026-05-09-12-guids.txt").write_text("g-first\ng-second\ng-third\n")
    (ws / "cycles" / "markets").mkdir(parents=True)
    (ws / "cycles" / "markets" / "2026-05-09-12.md").write_text(
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7, src:A001,A003]--> SPX | rate\n"
    )

    captured = {}

    def fake_apply(entities, relations, cycle_id, category=None):
        captured["relations"] = relations

    from unittest.mock import patch
    import newsparser.scripts.apply_graph as script
    with patch.object(script, "apply_graph_updates", side_effect=fake_apply):
        script.main(["apply_graph.py", "markets", "2026-05-09-12"])

    rels = captured["relations"]
    assert len(rels) == 1
    assert sorted(rels[0].source_article_guids) == ["g-first", "g-third"]


def test_resolver_drops_out_of_range_indices(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace"
    (ws / "input" / "markets").mkdir(parents=True)
    (ws / "input" / "markets" / "2026-05-09-12-guids.txt").write_text("g-only\n")
    (ws / "cycles" / "markets").mkdir(parents=True)
    (ws / "cycles" / "markets" / "2026-05-09-12.md").write_text(
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7, src:A001,A099]--> SPX | rate\n"
    )

    captured = {}

    def fake_apply(entities, relations, cycle_id, category=None):
        captured["relations"] = relations

    from unittest.mock import patch
    import newsparser.scripts.apply_graph as script
    with patch.object(script, "apply_graph_updates", side_effect=fake_apply):
        script.main(["apply_graph.py", "markets", "2026-05-09-12"])

    rels = captured["relations"]
    # A099 is out of range; only A001 → "g-only" survives
    assert rels[0].source_article_guids == ["g-only"]


def test_resolver_handles_missing_guids_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace"
    (ws / "cycles" / "markets").mkdir(parents=True)
    (ws / "cycles" / "markets" / "2026-05-09-12.md").write_text(
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7, src:A001]--> SPX | rate\n"
    )

    captured = {}

    def fake_apply(entities, relations, cycle_id, category=None):
        captured["relations"] = relations

    from unittest.mock import patch
    import newsparser.scripts.apply_graph as script
    with patch.object(script, "apply_graph_updates", side_effect=fake_apply):
        script.main(["apply_graph.py", "markets", "2026-05-09-12"])

    # No guids file → no resolution; source_article_guids stays empty.
    assert captured["relations"][0].source_article_guids == []
