import json
import pytest
from pathlib import Path
from unittest.mock import patch

from newsparser.mcp_server import graph_query, read_cycle_reports


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    (tmp_path / "workspace" / "me").mkdir(parents=True)
    (tmp_path / "workspace" / "me" / "interest-events.jsonl").touch()
    (tmp_path / "workspace" / "sessions").mkdir(parents=True)
    (tmp_path / "workspace" / "cycles").mkdir(parents=True)


def test_graph_query_returns_formatted_context():
    with patch("newsparser.mcp_server.get_context", return_value=[
        {"name": "삼성전자", "label": "Company", "mentions": 5}
    ]), patch("newsparser.mcp_server.get_influence_chain", return_value=[]):
        result = graph_query("삼성전자")
    assert "삼성전자" in result


def test_graph_query_logs_interest_event(tmp_path):
    events_path = Path(tmp_path / "workspace" / "me" / "interest-events.jsonl")
    with patch("newsparser.mcp_server.get_context", return_value=[]), \
         patch("newsparser.mcp_server.get_influence_chain", return_value=[]):
        graph_query("TSMC")
    event = json.loads(events_path.read_text().strip())
    assert "TSMC" in event["entities"]
    assert event["type"] == "query"


def test_read_cycle_reports_returns_n_most_recent(tmp_path):
    cycles = Path(tmp_path / "workspace" / "cycles")
    (cycles / "2026-05-04-10.md").write_text("cycle A")
    (cycles / "2026-05-05-10.md").write_text("cycle B")
    (cycles / "2026-05-06-10.md").write_text("cycle C")

    result = read_cycle_reports(n=2)
    assert "cycle B" in result
    assert "cycle C" in result
    assert "cycle A" not in result


def test_read_cycle_reports_empty_dir():
    result = read_cycle_reports()
    assert "No cycle reports found" in result
