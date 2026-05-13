# tests/test_bot_types.py
import re
import pytest
from newsparser.bots import Bot, Cron, TelegramMatch

async def _noop(ctx): pass

def test_bot_defaults():
    bot = Bot(name="test", triggers=[Cron("0 9 * * *")], run=_noop)
    assert bot.enabled is True
    assert bot.name == "test"

def test_bot_disabled():
    bot = Bot(name="test", triggers=[Cron("0 9 * * *")], run=_noop, enabled=False)
    assert not bot.enabled

def test_cron_default_tz():
    c = Cron("0 9 * * *")
    assert c.tz == "Asia/Seoul"

def test_cron_custom_tz():
    c = Cron("0 9 * * *", tz="UTC")
    assert c.tz == "UTC"

def test_telegram_match_pattern():
    t = TelegramMatch(r"^/cycle\b")
    assert re.search(t.pattern, "/cycle")
    assert not re.search(t.pattern, "/cycleXYZ")

def test_telegram_match_catch_all():
    t = TelegramMatch(r".*")
    assert re.search(t.pattern, "anything")
