import pytest
from pathlib import Path
from unittest.mock import patch
from newsparser.bot.tracker import run_tracker, load_history, save_history


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    (tmp_path / "workspace" / "sessions").mkdir(parents=True)
    (tmp_path / "workspace" / "me").mkdir(parents=True)


def test_load_history_empty_for_new_chat():
    history = load_history("chat123")
    assert history == []


def test_save_and_load_history():
    save_history("chat123", [
        {"role": "user", "content": "안녕"},
        {"role": "assistant", "content": "안녕하세요"},
    ])
    history = load_history("chat123")
    assert len(history) == 2
    assert history[0]["content"] == "안녕"


def test_load_history_returns_last_10_turns():
    turns = [{"role": "user", "content": str(i), "ts": "2026-05-05T00:00:00"} for i in range(15)]
    save_history("chat123", turns)
    history = load_history("chat123")
    assert len(history) == 10
    assert history[0]["content"] == "5"


def test_run_tracker_calls_claude_with_mcp_config():
    with patch("newsparser.bot.tracker.run_claude", return_value="Claude answer") as mock_claude:
        answer = run_tracker(chat_id="chat123", query="FOMC 어떻게 됐어?")
    mock_claude.assert_called_once()
    args, kwargs = mock_claude.call_args
    prompt = args[0]
    assert "FOMC" in prompt
    assert kwargs.get("mcp_config") is not None
    assert answer == "Claude answer"


def test_run_tracker_appends_to_history():
    with patch("newsparser.bot.tracker.run_claude", return_value="답변"):
        run_tracker(chat_id="chat123", query="질문")
    history = load_history("chat123")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
