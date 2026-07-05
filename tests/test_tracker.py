import pytest
from unittest.mock import patch
from newsparser.bot.tracker import run_tracker, load_history
from newsparser.store import conversations as conv


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("CONV_DB_PATH", str(tmp_path / "conversations.db"))
    conv.init_conv_db()


def test_load_history_empty_for_new_chat():
    history = load_history("chat123")
    assert history == []


def test_save_and_load_history():
    conv.add_message("chat123", "user", "안녕")
    conv.add_message("chat123", "assistant", "안녕하세요")
    history = load_history("chat123")
    assert len(history) == 2
    assert history[0]["content"] == "안녕"


def test_load_history_returns_last_10_turns():
    for i in range(15):
        conv.add_message("chat123", "user", str(i), ts=f"2026-05-05T00:00:{i:02d}")
    history = load_history("chat123")
    assert len(history) == 10
    assert history[0]["content"] == "5"


def test_run_tracker_calls_claude_with_mcp_config():
    with patch("newsparser.bot.tracker.classify_query", return_value="both"), \
         patch("newsparser.bot.tracker.run_claude", return_value="Claude answer") as mock_claude:
        answer = run_tracker(chat_id="chat123", query="FOMC 어떻게 됐어?")
    mock_claude.assert_called_once()
    args, kwargs = mock_claude.call_args
    prompt = args[0]
    assert "FOMC" in prompt
    assert kwargs.get("mcp_config") is not None
    assert answer == "Claude answer"


def test_run_tracker_appends_to_history():
    with patch("newsparser.bot.tracker.classify_query", return_value="both"), \
         patch("newsparser.bot.tracker.run_claude", return_value="답변"):
        run_tracker(chat_id="chat123", query="질문")
    history = load_history("chat123")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


def test_run_tracker_injects_category_hint():
    captured: dict = {}

    def fake_run_claude(prompt, **kw):
        captured["prompt"] = prompt
        return "answer"

    with patch("newsparser.bot.tracker.classify_query", return_value="tech") as mock_classify, \
         patch("newsparser.bot.tracker.run_claude", side_effect=fake_run_claude):
        run_tracker(chat_id="t1", query="OpenAI 새 모델 어때?")

    mock_classify.assert_called_once_with("OpenAI 새 모델 어때?", history=None)
    assert "카테고리 힌트" in captured["prompt"]
    assert "tech" in captured["prompt"]


def test_run_tracker_continues_if_classify_query_fails():
    def fake_run_claude(prompt, **kw):
        return "answer"

    with patch("newsparser.bot.tracker.classify_query", side_effect=RuntimeError("boom")), \
         patch("newsparser.bot.tracker.run_claude", side_effect=fake_run_claude):
        # must not raise — the tracker should treat classification as best-effort
        result = run_tracker(chat_id="t1", query="anything")
    assert result == "answer"


def test_ignore_marker_registered():
    from newsparser.bot.tracker import _ADMIN_MARKERS
    assert "ignore.md updated" in _ADMIN_MARKERS


def test_ignore_marker_skips_history_save(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    import newsparser.bot.tracker as tracker

    monkeypatch.setattr(tracker, "run_claude",
                        lambda *a, **k: "추가했습니다. ignore.md updated")
    monkeypatch.setattr(tracker, "classify_query", lambda *a, **k: "both")

    tracker.run_tracker("chat-xyz", "무시: Opus 4.8 API 미등장")

    # admin marker present → conversation history must NOT be saved
    assert tracker.load_history("chat-xyz") == []
