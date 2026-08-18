"""Chat-path routing: a YouTube link goes to Gemini, everything else to Claude."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import newsparser.bots.tracker.bot as tracker_bot
from newsparser.gemini import GeminiError
from newsparser.store import conversations as conv


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("CONV_DB_PATH", str(tmp_path / "conversations.db"))
    conv.init_conv_db()


class _Sender:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


def _ctx(text):
    from newsparser.bots import Context

    sender = _Sender()
    return Context(
        bot_name="tracker",
        workspace=SimpleNamespace(),
        telegram=sender,
        message=SimpleNamespace(text=text, chat_id=42),
    ), sender


async def test_plain_question_goes_to_the_tracker():
    ctx, sender = _ctx("FOMC 어떻게 됐어?")
    with patch.object(tracker_bot, "run_tracker", return_value="클로드 답변") as claude, \
         patch.object(tracker_bot, "run_youtube") as gem:
        await tracker_bot.run(ctx)

    claude.assert_called_once()
    gem.assert_not_called()
    assert sender.sent == ["클로드 답변"]


async def test_youtube_link_goes_to_gemini_with_the_parsed_url():
    ctx, sender = _ctx("이거 봐줘 https://youtu.be/dQw4w9WgXcQ?t=10 3분대 위주로")
    with patch.object(tracker_bot, "run_tracker") as claude, \
         patch.object(tracker_bot, "run_youtube", return_value="영상 요약") as gem:
        await tracker_bot.run(ctx)

    claude.assert_not_called()
    assert gem.call_args.kwargs["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert gem.call_args.kwargs["instruction"] == "이거 봐줘 3분대 위주로"
    assert gem.call_args.kwargs["chat_id"] == "42"
    assert sender.sent == ["영상 요약"]


async def test_gemini_failure_reports_instead_of_falling_back_to_claude():
    """Claude cannot watch the video, so a fallback answer would be about the
    link rather than its contents — the user gets told the truth instead."""
    ctx, sender = _ctx("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    with patch.object(tracker_bot, "run_tracker") as claude, \
         patch.object(tracker_bot, "run_youtube",
                      side_effect=GeminiError("GCP 키 파일이 없습니다: gcp-key.json")):
        await tracker_bot.run(ctx)

    claude.assert_not_called()
    assert len(sender.sent) == 1
    assert "유튜브 분석에 실패했습니다" in sender.sent[0]
    assert "gcp-key.json" in sender.sent[0]


async def test_rebuild_command_still_short_circuits():
    ctx, sender = _ctx("/rebuild")
    with patch.object(tracker_bot, "_docker_rebuild") as rebuild, \
         patch.object(tracker_bot, "run_tracker") as claude, \
         patch.object(tracker_bot, "run_youtube") as gem:
        await tracker_bot.run(ctx)

    rebuild.assert_called_once()
    claude.assert_not_called()
    gem.assert_not_called()
