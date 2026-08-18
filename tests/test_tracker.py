import pytest
import re
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
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


def test_run_tracker_prompt_routes_run_orders_to_start_job():
    """"사이클 돌려줘" must reach start_job, not the /cycle slash command.

    The tracker runs with bypassPermissions, so the project's .claude/commands/
    cycle.md is visible to it — and cycle.md's first job is parsing $ARGUMENTS
    into slot+category. Without an explicit ban the model picks that over
    start_job and asks the user for a slot, which start_job doesn't even take.
    """
    captured: dict = {}

    def fake_run_claude(prompt, **kw):
        captured["prompt"] = prompt
        return "answer"

    with patch("newsparser.bot.tracker.classify_query", return_value="tech"), \
         patch("newsparser.bot.tracker.run_claude", side_effect=fake_run_claude):
        run_tracker(chat_id="t1", query="사이클 돌려줘")

    prompt = captured["prompt"]
    assert "slot·category를 되묻지 마라" in prompt
    assert "슬래시 커맨드를 직접 실행하는 것도 금지" in prompt
    assert "실행 지시에는 언제나 start_job만 쓴다" in prompt


def test_run_tracker_prompt_supplies_kst_clock():
    """The prompt must carry a KST wall clock.

    run_claude passes no TZ/date to the CLI subprocess, so the model's only clock
    is the host's — and nothing in deploy/ pins the host to Asia/Seoul. Without
    an interpolated value "오늘" resolves to the previous KST day on a UTC host
    between 00:00 and 09:00 KST.
    """
    captured: dict = {}

    def fake_run_claude(prompt, **kw):
        captured["prompt"] = prompt
        return "answer"

    with patch("newsparser.bot.tracker.classify_query", return_value="markets"), \
         patch("newsparser.bot.tracker.run_claude", side_effect=fake_run_claude):
        run_tracker(chat_id="t1", query="마지막 사이클 언제 돌았어?")

    # Matched by shape + KST date, not by an exact HH:MM equal to "now" — the
    # minute can tick over between prompt build and assertion.
    m = re.search(r"지금은 (\d{4}-\d{2}-\d{2}) \d{2}:\d{2} KST다", captured["prompt"])
    assert m, captured["prompt"][:400]
    assert m.group(1) == datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")


def test_run_tracker_prompt_flags_non_kst_tool_timestamps():
    """Per-source timezones, not a blanket "everything is KST".

    market_query's 1h `ts` is UTC (market/fetcher.py), its 1d `date` is the
    market's session date, and the conversation tools render UTC timestamps
    verbatim — only cycle slots and job_status are KST.
    """
    captured: dict = {}

    def fake_run_claude(prompt, **kw):
        captured["prompt"] = prompt
        return "answer"

    with patch("newsparser.bot.tracker.classify_query", return_value="markets"), \
         patch("newsparser.bot.tracker.run_claude", side_effect=fake_run_claude):
        run_tracker(chat_id="t1", query="어제 SPX 몇 시에 빠졌어?")

    prompt = captured["prompt"]
    assert "`ts`(freq=\"1h\") — UTC다" in prompt
    assert "KST 날짜가 아니다" in prompt
    assert "`[타임스탬프]` — UTC다" in prompt


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


def test_run_tracker_runs_on_opus():
    """Chat answers opt up to opus; the cron jobs keep run_claude's sonnet default."""
    with patch("newsparser.bot.tracker.classify_query", return_value="both"), \
         patch("newsparser.bot.tracker.run_claude", return_value="답변입니다") as mock_claude:
        run_tracker(chat_id="chat123", query="질문")
    assert mock_claude.call_args.kwargs["model"] == "claude-opus-5"
