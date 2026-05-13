# tests/test_bot_context.py
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from newsparser.bots.core.context import Context, TelegramSender


def make_ctx(bot=None, chat_id=None, alert_chat_id=""):
    sender = TelegramSender(bot=bot, chat_id=chat_id, alert_chat_id=alert_chat_id)
    return Context(bot_name="test", workspace=Path("workspace"), telegram=sender)


def test_context_logger_name():
    ctx = make_ctx()
    assert ctx.logger.name == "newsparser.bots.test"


def test_telegram_sender_uses_chat_id():
    mock_bot = AsyncMock()
    sender = TelegramSender(bot=mock_bot, chat_id="123", alert_chat_id="999")
    asyncio.get_event_loop().run_until_complete(sender.send("hello"))
    mock_bot.send_message.assert_called_once_with(chat_id="123", text="hello")


def test_telegram_sender_falls_back_to_alert_chat_id():
    mock_bot = AsyncMock()
    sender = TelegramSender(bot=mock_bot, chat_id=None, alert_chat_id="999")
    asyncio.get_event_loop().run_until_complete(sender.send("hello"))
    mock_bot.send_message.assert_called_once_with(chat_id="999", text="hello")


def test_telegram_sender_truncates_long_text():
    mock_bot = AsyncMock()
    sender = TelegramSender(bot=mock_bot, chat_id="123")
    long_text = "x" * 5000
    asyncio.get_event_loop().run_until_complete(sender.send(long_text))
    sent = mock_bot.send_message.call_args[1]["text"]
    assert len(sent) <= 4096


def test_telegram_sender_does_nothing_without_bot():
    sender = TelegramSender(bot=None, chat_id="123")
    # Should not raise
    asyncio.get_event_loop().run_until_complete(sender.send("hello"))


@pytest.mark.asyncio
async def test_context_run_in_thread():
    ctx = make_ctx()
    result = await ctx.run_in_thread(lambda x: x * 2, 5)
    assert result == 10
