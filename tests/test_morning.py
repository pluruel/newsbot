import os
import pytest
from pathlib import Path
from unittest.mock import patch
from newsparser.scheduler.morning import run_morning

SAMPLE_BRIEF = """🌅 Daily Brief — 2026-05-05 (Tuesday)

[1] 🏦 Fed cuts rates 50bp surprise
    ↳ KOSPI 급등 예상, 환율 변동 주목

질문이나 추적은 답장으로."""

@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    # Create a cycle file so the prompt has something to reference
    cycles = tmp_path / "workspace" / "cycles"
    cycles.mkdir(parents=True)
    (cycles / "2026-05-05-00.md").write_text("# Cycle content")

def test_run_morning_calls_claude_and_sends_telegram():
    with patch("newsparser.scheduler.morning.run_claude", return_value=SAMPLE_BRIEF) as mock_claude, \
         patch("newsparser.scheduler.morning.send_message") as mock_send:
        run_morning("2026-05-05")
    mock_claude.assert_called_once()
    mock_send.assert_called_once_with(SAMPLE_BRIEF)

def test_run_morning_saves_brief_file(tmp_path):
    workspace = Path(os.environ["WORKSPACE_DIR"])
    with patch("newsparser.scheduler.morning.run_claude", return_value=SAMPLE_BRIEF), \
         patch("newsparser.scheduler.morning.send_message"):
        run_morning("2026-05-05")
    brief = workspace / "briefs" / "2026-05-05.md"
    assert brief.exists()
    assert "Fed cuts rates" in brief.read_text()

def test_run_morning_retries_on_send_failure():
    call_count = {"n": 0}
    def flaky_send(text):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError("Telegram unavailable")
    with patch("newsparser.scheduler.morning.run_claude", return_value=SAMPLE_BRIEF), \
         patch("newsparser.scheduler.morning.send_message", side_effect=flaky_send):
        run_morning("2026-05-05")  # succeeds on 3rd attempt
    assert call_count["n"] == 3

def test_run_morning_calls_interests_rollup_before_brief():
    call_order = []
    def fake_rollup():
        call_order.append("rollup")
    def fake_ensure():
        call_order.append("workspace")
        ws = Path(os.environ["WORKSPACE_DIR"])
        for subdir in ["cycles", "briefs", "input", "me", "state", "logs", "sessions"]:
            (ws / subdir).mkdir(parents=True, exist_ok=True)
        return ws
    def fake_claude(prompt):
        call_order.append("claude")
        return SAMPLE_BRIEF
    with patch("newsparser.scheduler.morning.interests_rollup", side_effect=fake_rollup), \
         patch("newsparser.scheduler.morning.ensure_workspace", side_effect=fake_ensure), \
         patch("newsparser.scheduler.morning.run_claude", side_effect=fake_claude), \
         patch("newsparser.scheduler.morning.send_message"):
        run_morning("2026-05-05")
    assert call_order == ["rollup", "workspace", "claude"]

def test_run_morning_continues_if_rollup_fails():
    with patch("newsparser.scheduler.morning.interests_rollup", side_effect=RuntimeError("rollup error")), \
         patch("newsparser.scheduler.morning.run_claude", return_value=SAMPLE_BRIEF) as mock_claude, \
         patch("newsparser.scheduler.morning.send_message"):
        run_morning("2026-05-05")  # must not raise
    mock_claude.assert_called_once()
