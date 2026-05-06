import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch
from newsparser.bot.tracker import run_tracker, load_history, save_history

@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    (tmp_path / "workspace" / "sessions").mkdir(parents=True)
    (tmp_path / "workspace" / "me").mkdir(parents=True)
    (tmp_path / "workspace" / "me" / "interest-events.jsonl").touch()

def test_load_history_empty_for_new_chat(tmp_path):
    history = load_history("chat123")
    assert history == []

def test_save_and_load_history(tmp_path):
    save_history("chat123", [
        {"role": "user", "content": "안녕"},
        {"role": "assistant", "content": "안녕하세요"},
    ])
    history = load_history("chat123")
    assert len(history) == 2
    assert history[0]["content"] == "안녕"

def test_load_history_returns_last_10_turns(tmp_path):
    turns = [{"role": "user", "content": str(i), "ts": "2026-05-05T00:00:00"} for i in range(15)]
    save_history("chat123", turns)
    history = load_history("chat123")
    assert len(history) == 10
    assert history[0]["content"] == "5"  # 마지막 10개

def test_run_tracker_calls_claude_with_context():
    with patch("newsparser.bot.tracker.get_context", return_value=[]), \
         patch("newsparser.bot.tracker.get_influence_chain", return_value=[]), \
         patch("newsparser.bot.tracker.run_claude", return_value="Claude answer") as mock_claude:
        answer = run_tracker(chat_id="chat123", query="FOMC 어떻게 됐어?")
    mock_claude.assert_called_once()
    prompt = mock_claude.call_args[0][0]
    assert "FOMC" in prompt
    assert answer == "Claude answer"

def test_run_tracker_appends_to_history():
    workspace = Path(os.environ["WORKSPACE_DIR"])
    with patch("newsparser.bot.tracker.get_context", return_value=[]), \
         patch("newsparser.bot.tracker.get_influence_chain", return_value=[]), \
         patch("newsparser.bot.tracker.run_claude", return_value="답변"):
        run_tracker(chat_id="chat123", query="질문")
    history = load_history("chat123")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"

def test_run_tracker_logs_interest_event():
    workspace = Path(os.environ["WORKSPACE_DIR"])
    with patch("newsparser.bot.tracker.get_context", return_value=[]), \
         patch("newsparser.bot.tracker.get_influence_chain", return_value=[]), \
         patch("newsparser.bot.tracker.run_claude", return_value="답변"):
        run_tracker(chat_id="chat123", query="반도체 섹터 동향")
    events = (workspace / "me" / "interest-events.jsonl").read_text()
    assert "query" in events

def test_log_interest_event_includes_graph_entities():
    workspace = Path(os.environ["WORKSPACE_DIR"])
    with patch("newsparser.bot.tracker.get_context", return_value=[
            {"name": "삼성전자", "label": "Company", "mentions": 5},
            {"name": "TSMC", "label": "Company", "mentions": 3},
         ]), \
         patch("newsparser.bot.tracker.get_influence_chain", return_value=[]), \
         patch("newsparser.bot.tracker.run_claude", return_value="답변"):
        run_tracker(chat_id="chat123", query="반도체 업황")
    events_path = workspace / "me" / "interest-events.jsonl"
    event = json.loads(events_path.read_text().strip().splitlines()[-1])
    assert "삼성전자" in event["entities"]
    assert "TSMC" in event["entities"]

def test_log_interest_event_empty_entities_on_graph_failure():
    workspace = Path(os.environ["WORKSPACE_DIR"])
    with patch("newsparser.bot.tracker.get_context", side_effect=RuntimeError("DB down")), \
         patch("newsparser.bot.tracker.get_influence_chain", return_value=[]), \
         patch("newsparser.bot.tracker.run_claude", return_value="답변"):
        run_tracker(chat_id="chat123", query="반도체 업황")
    event = json.loads((workspace / "me" / "interest-events.jsonl").read_text().strip().splitlines()[-1])
    assert event["entities"] == []
