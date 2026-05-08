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
