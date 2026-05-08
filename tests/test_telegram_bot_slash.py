# tests/test_telegram_bot_slash.py
import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


def _make_update(text: str, chat_id: str = "12345") -> MagicMock:
    update = MagicMock()
    update.message.text = text
    update.message.chat_id = chat_id
    update.message.reply_text = AsyncMock()
    return update


def test_slash_cycle_calls_run_cycle_script(monkeypatch):
    monkeypatch.setenv("ALLOWED_CHAT_ID", "12345")

    from newsparser.bot.telegram_bot import handle_message

    with patch("newsparser.bot.telegram_bot.run_cycle_script") as mock_cycle, \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
        update = _make_update("/cycle")
        asyncio.run(handle_message(update, MagicMock()))

    mock_cycle.assert_called_once()


def test_slash_weekly_calls_run_weekly_script(monkeypatch):
    monkeypatch.setenv("ALLOWED_CHAT_ID", "12345")

    from newsparser.bot.telegram_bot import handle_message

    with patch("newsparser.bot.telegram_bot.run_weekly_script") as mock_weekly, \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
        update = _make_update("/weekly")
        asyncio.run(handle_message(update, MagicMock()))

    mock_weekly.assert_called_once()


def test_slash_reflect_calls_run_reflect_script(monkeypatch):
    monkeypatch.setenv("ALLOWED_CHAT_ID", "12345")

    from newsparser.bot.telegram_bot import handle_message

    with patch("newsparser.bot.telegram_bot.run_reflect_script") as mock_reflect, \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
        update = _make_update("/reflect")
        asyncio.run(handle_message(update, MagicMock()))

    mock_reflect.assert_called_once()
