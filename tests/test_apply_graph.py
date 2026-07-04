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


def test_apply_graph_calls_annotate_after_apply(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace"
    (ws / "cycles" / "markets").mkdir(parents=True)
    (ws / "cycles" / "markets" / "2026-05-09-12.md").write_text(
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7]--> SPX | rate\n"
    )

    order: list[str] = []

    def fake_apply(*a, **kw):
        order.append("apply")

    def fake_annotate(relations, slot, category):
        order.append("annotate")
        assert slot == "2026-05-09-12"
        assert category == "markets"
        return 1

    from unittest.mock import patch
    import newsparser.scripts.apply_graph as script
    with patch.object(script, "apply_graph_updates", side_effect=fake_apply), \
         patch.object(script, "maybe_annotate_impacts", side_effect=fake_annotate):
        script.main(["apply_graph.py", "markets", "2026-05-09-12"])

    assert order == ["apply", "annotate"]


def test_apply_graph_annotate_failure_doesnt_break_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace"
    (ws / "cycles" / "markets").mkdir(parents=True)
    (ws / "cycles" / "markets" / "2026-05-09-12.md").write_text(
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7]--> SPX | rate\n"
    )

    from unittest.mock import patch
    import newsparser.scripts.apply_graph as script
    with patch.object(script, "apply_graph_updates"), \
         patch.object(script, "maybe_annotate_impacts", side_effect=RuntimeError("boom")):
        # Must not raise
        script.main(["apply_graph.py", "markets", "2026-05-09-12"])


SAMPLE_REPORT_WITH_IGNORED = """\
## Graph updates
### Entities
- NEW | Company | OpenAI | aliases: []
- NEW | Event | GPT-5 | aliases: []

### Relations
- NEW | OpenAI --ANNOUNCED[conf:0.9, impact:0.8]--> GPT-5 | announced new model
"""


def test_apply_graph_drops_ignored_entities_and_relations(tmp_path):
    ws = Path(os.environ["WORKSPACE_DIR"])
    (ws / "me").mkdir(parents=True, exist_ok=True)
    (ws / "me" / "ignore.md").write_text(
        "| 종류 | 대상 | 추가일 | 메모 |\n"
        "|------|------|--------|------|\n"
        "| entity | GPT-5 | 2026-06-28 |  |\n",
        encoding="utf-8",
    )
    (ws / "cycles" / "tech" / "2026-05-08-12.md").write_text(SAMPLE_REPORT_WITH_IGNORED)

    with patch("newsparser.scripts.apply_graph.apply_graph_updates") as mock_apply:
        script.main(["apply_graph.py", "tech", "2026-05-08-12"])

    entities, relations = mock_apply.call_args.args
    # Ignored entity and the relation referencing it are dropped.
    assert all(e.name != "GPT-5" for e in entities)
    assert relations == []
    # Non-ignored entity survives.
    assert any(e.name == "OpenAI" for e in entities)


def test_apply_graph_no_ignore_file_keeps_everything(tmp_path):
    ws = Path(os.environ["WORKSPACE_DIR"])
    (ws / "cycles" / "tech" / "2026-05-08-12.md").write_text(SAMPLE_REPORT_WITH_IGNORED)

    with patch("newsparser.scripts.apply_graph.apply_graph_updates") as mock_apply:
        script.main(["apply_graph.py", "tech", "2026-05-08-12"])

    entities, relations = mock_apply.call_args.args
    assert len(entities) == 2
    assert len(relations) == 1


SAMPLE_REPORT_RENAMEABLE = """\
## Graph updates
### Entities
- NEW | Company | 테슬라 | aliases: []

### Relations
- NEW | 테슬라 --ANNOUNCED[conf:0.9, impact:0.8]--> Robotaxi | launch
"""


def test_apply_graph_renames_entities_and_relations_via_resolver(tmp_path):
    ws = Path(os.environ["WORKSPACE_DIR"])
    (ws / "cycles" / "tech" / "2026-05-08-12.md").write_text(SAMPLE_REPORT_RENAMEABLE)

    with patch("newsparser.scripts.apply_graph.apply_graph_updates") as mock_apply, \
         patch("newsparser.graph.resolver.resolve_entities",
               return_value={"테슬라": "Tesla"}):
        script.main(["apply_graph.py", "tech", "2026-05-08-12"])

    entities, relations = mock_apply.call_args.args
    assert entities[0].name == "Tesla"
    assert relations[0].subject == "Tesla"


def test_apply_graph_resolver_noop_when_no_renames(tmp_path):
    ws = Path(os.environ["WORKSPACE_DIR"])
    (ws / "cycles" / "tech" / "2026-05-08-12.md").write_text(SAMPLE_REPORT)

    with patch("newsparser.scripts.apply_graph.apply_graph_updates") as mock_apply, \
         patch("newsparser.graph.resolver.resolve_entities", return_value={}):
        script.main(["apply_graph.py", "tech", "2026-05-08-12"])

    entities, relations = mock_apply.call_args.args
    assert entities[0].name == "OpenAI"


SAMPLE_REPORT_ALIAS_IGNORED = """\
## Graph updates
### Entities
- NEW | Company | Claude | aliases: [opus 4.8 preview]
- NEW | Company | OpenAI | aliases: []

### Relations
- NEW | Claude --ANNOUNCED[conf:0.9, impact:0.8]--> Thing | a
- NEW | OpenAI --ANNOUNCED[conf:0.9, impact:0.8]--> Other | b
"""


def test_apply_graph_drops_relations_of_alias_matched_entity(tmp_path):
    """An entity dropped only via an ALIAS match must also lose its relations."""
    ws = Path(os.environ["WORKSPACE_DIR"])
    (ws / "me").mkdir(parents=True, exist_ok=True)
    (ws / "me" / "ignore.md").write_text(
        "| 종류 | 대상 | 추가일 | 메모 |\n"
        "|------|------|--------|------|\n"
        "| entity | opus 4.8 preview | 2026-06-28 |  |\n",
        encoding="utf-8",
    )
    (ws / "cycles" / "tech" / "2026-05-08-12.md").write_text(SAMPLE_REPORT_ALIAS_IGNORED)

    with patch("newsparser.scripts.apply_graph.apply_graph_updates") as mock_apply:
        script.main(["apply_graph.py", "tech", "2026-05-08-12"])

    entities, relations = mock_apply.call_args.args
    # Claude is dropped via its alias; its relation must be dropped too.
    assert all(e.name != "Claude" for e in entities)
    assert all(r.subject != "Claude" for r in relations)
    # OpenAI and its relation survive.
    assert any(e.name == "OpenAI" for e in entities)
    assert any(r.subject == "OpenAI" for r in relations)
