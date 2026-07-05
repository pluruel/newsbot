import os
from pathlib import Path
from unittest.mock import patch

import scripts.restore_graph_from_cycles as script


SAMPLE_REPORT = """\
## Graph updates
### Entities
- NEW | Company | 테슬라 | aliases: []

### Relations
- NEW | 테슬라 --ANNOUNCED[conf:0.9, impact:0.8]--> Robotaxi | launch
"""


def _write_report(workspace: Path, category: str, slot: str, body: str) -> None:
    d = workspace / "cycles" / category
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slot}.md").write_text(body, encoding="utf-8")


def test_restore_skips_missing_category_dir(tmp_path, capsys):
    with patch.object(script, "apply_graph_updates") as mock_apply:
        script.restore(tmp_path)
    mock_apply.assert_not_called()
    out = capsys.readouterr().out
    assert "[skip]" in out


def test_restore_replays_reports_in_chronological_order(tmp_path):
    _write_report(tmp_path, "tech", "2026-05-08-08", SAMPLE_REPORT)
    _write_report(tmp_path, "tech", "2026-05-08-12", SAMPLE_REPORT)

    calls = []
    with patch.object(script, "apply_graph_updates",
                       side_effect=lambda *a, **kw: calls.append(kw.get("cycle_id"))), \
         patch("newsparser.graph.resolver.resolve_entities", return_value={}):
        script.restore(tmp_path)

    assert calls == ["tech-2026-05-08-08", "tech-2026-05-08-12"]


def test_restore_routes_through_resolver_renames(tmp_path):
    """A replayed report's entities/relations must reflect resolver renames —
    this is the whole point of routing replay through prepare_graph_updates
    instead of calling apply_graph_updates directly."""
    _write_report(tmp_path, "tech", "2026-05-08-12", SAMPLE_REPORT)

    captured = {}

    def fake_apply(entities, relations, cycle_id, category=None):
        captured["entities"] = entities
        captured["relations"] = relations

    with patch.object(script, "apply_graph_updates", side_effect=fake_apply), \
         patch("newsparser.graph.resolver.resolve_entities",
               return_value={"테슬라": ("Tesla", "Company")}):
        script.restore(tmp_path)

    assert captured["entities"][0].name == "Tesla"
    assert captured["relations"][0].subject == "Tesla"


def test_restore_drops_ignored_entities(tmp_path):
    (tmp_path / "me").mkdir(parents=True)
    (tmp_path / "me" / "ignore.md").write_text(
        "| 종류 | 대상 | 추가일 | 메모 |\n"
        "|------|------|--------|------|\n"
        "| entity | Robotaxi | 2026-06-28 |  |\n",
        encoding="utf-8",
    )
    _write_report(tmp_path, "tech", "2026-05-08-12", SAMPLE_REPORT)

    captured = {}

    def fake_apply(entities, relations, cycle_id, category=None):
        captured["entities"] = entities
        captured["relations"] = relations

    with patch.object(script, "apply_graph_updates", side_effect=fake_apply), \
         patch("newsparser.graph.resolver.resolve_entities", return_value={}):
        script.restore(tmp_path)

    assert captured["relations"] == []
