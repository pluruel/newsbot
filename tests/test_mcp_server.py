import json
import pytest
from pathlib import Path
from unittest.mock import patch

from newsparser.mcp_server import graph_query, read_cycle_reports, read_conversation_history, read_interests
from newsparser.bot.tracker import save_history


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
    cycles = Path(tmp_path / "workspace" / "cycles" / "markets")
    cycles.mkdir(parents=True, exist_ok=True)
    (cycles / "2026-05-04-10.md").write_text("cycle A")
    (cycles / "2026-05-05-10.md").write_text("cycle B")
    (cycles / "2026-05-06-10.md").write_text("cycle C")

    result = read_cycle_reports(category="markets", n=2)
    assert "cycle B" in result
    assert "cycle C" in result
    assert "cycle A" not in result


def test_read_cycle_reports_empty_dir():
    result = read_cycle_reports(category="markets")
    assert "No cycle reports found" in result


def test_read_conversation_history_returns_formatted_turns(tmp_path):
    save_history("chat99", [
        {"role": "user", "content": "안녕", "ts": "2026-05-05T00:00:00"},
        {"role": "assistant", "content": "안녕하세요", "ts": "2026-05-05T00:00:01"},
    ])
    result = read_conversation_history("chat99")
    assert "USER: 안녕" in result
    assert "ASSISTANT: 안녕하세요" in result


def test_read_conversation_history_empty():
    result = read_conversation_history("nonexistent_chat")
    assert "No conversation history" in result


def test_read_interests_returns_content(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)
    (me / "interests_tech.md").write_text("# Tech profile\n")
    result = read_interests(category="tech")
    assert "Tech profile" in result


def test_read_interests_missing_file():
    result = read_interests(category="tech")
    assert "No interests file found" in result


from newsparser.mcp_server import (
    graph_query, read_cycle_reports, read_conversation_history, read_interests,
    write_interests, get_interest_weights, classify_query as mcp_classify_query,
)


def test_read_cycle_reports_reads_per_category_subfolder(tmp_path):
    base = Path(tmp_path / "workspace" / "cycles")
    (base / "tech").mkdir(parents=True, exist_ok=True)
    (base / "markets").mkdir(parents=True, exist_ok=True)
    (base / "tech" / "2026-05-07-12.md").write_text("tech cycle X")
    (base / "markets" / "2026-05-07-12.md").write_text("markets cycle Y")

    tech = read_cycle_reports(category="tech", n=2)
    assert "tech cycle X" in tech
    assert "markets cycle Y" not in tech


def test_graph_query_passes_category_filter():
    with patch("newsparser.mcp_server.get_context", return_value=[]) as mock_ctx, \
         patch("newsparser.mcp_server.get_influence_chain", return_value=[]):
        graph_query("OpenAI", category="tech")
    _, kwargs = mock_ctx.call_args
    args = mock_ctx.call_args[0]
    # accept either calling convention
    assert kwargs.get("category") == "tech" or "tech" in args


def test_read_interests_reads_per_category_file(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)
    (me / "interests_tech.md").write_text("# Tech profile\n")
    (me / "interests_markets.md").write_text("# Markets profile\n")

    assert "Tech profile" in read_interests(category="tech")
    assert "Markets profile" in read_interests(category="markets")


def test_write_interests_writes_to_per_category_file(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)

    write_interests(category="tech", content="# new tech profile\n")
    assert (me / "interests_tech.md").read_text() == "# new tech profile\n"


def test_get_interest_weights_uses_per_category_file(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)
    (me / "interests_tech.md").write_text(
        "| Theme | interest_weight | familiarity_weight | Notes |\n"
        "|---|---|---|---|\n"
        "| AI | 0.95 | 0.5 | |\n"
    )
    (me / "interest-events.jsonl").write_text("")
    out = get_interest_weights(category="tech", days=14)
    assert "AI" in out
    assert "0.95" in out


def test_classify_query_tool_returns_label():
    with patch("newsparser.mcp_server.classify_query", return_value="tech"):
        # The MCP-exported function delegates to the same name in `classifier`
        result = mcp_classify_query("OpenAI 신모델 동향")
    assert result == "tech"
