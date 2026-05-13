# tests/test_bot_registry.py
import textwrap
from pathlib import Path
import pytest
from newsparser.bots.core.registry import BotRegistry
from newsparser.bots.core.types import Cron, TelegramMatch


def _write_bot(bots_dir: Path, name: str, enabled: bool = True) -> None:
    pkg_dir = bots_dir / name
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("")
    enabled_str = str(enabled)
    (pkg_dir / "bot.py").write_text(textwrap.dedent(f"""
        from newsparser.bots.core.types import Bot, Cron
        async def _run(ctx): pass
        BOT = Bot(name="{name}", triggers=[Cron('0 9 * * *')], run=_run, enabled={enabled_str})
    """))


def test_registry_loads_enabled_bots(tmp_path):
    _write_bot(tmp_path, "alpha")
    _write_bot(tmp_path, "beta")
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    assert sorted(registry.names()) == ["alpha", "beta"]


def test_registry_skips_disabled(tmp_path):
    _write_bot(tmp_path, "alpha", enabled=True)
    _write_bot(tmp_path, "beta", enabled=False)
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    assert registry.names() == ["alpha"]


def test_registry_reload_picks_up_changes(tmp_path):
    _write_bot(tmp_path, "alpha")
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    assert "alpha" in registry.names()
    _write_bot(tmp_path, "gamma")
    registry.load()
    assert "gamma" in registry.names()


def test_cron_bots_returns_cron_triggers(tmp_path):
    _write_bot(tmp_path, "alpha")
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    pairs = registry.cron_bots()
    assert len(pairs) == 1
    bot, trigger = pairs[0]
    assert bot.name == "alpha"
    assert isinstance(trigger, Cron)


def test_telegram_bots_returns_telegram_triggers(tmp_path):
    pkg_dir = tmp_path / "mybot"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "bot.py").write_text(textwrap.dedent("""
        from newsparser.bots.core.types import Bot, TelegramMatch
        async def _run(ctx): pass
        BOT = Bot(name="mybot", triggers=[TelegramMatch(r"^/foo")], run=_run)
    """))
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    pairs = registry.telegram_bots()
    assert len(pairs) == 1
    bot, trigger = pairs[0]
    assert isinstance(trigger, TelegramMatch)


def test_telegram_bots_catch_all_is_last(tmp_path):
    """Catch-all '.*' must come after specific patterns regardless of directory sort order."""
    for name, pattern in [("aaa", r".*"), ("zzz", r"^/specific")]:
        pkg_dir = tmp_path / name
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "bot.py").write_text(textwrap.dedent(f"""
            from newsparser.bots.core.types import Bot, TelegramMatch
            async def _run(ctx): pass
            BOT = Bot(name="{name}", triggers=[TelegramMatch(r"{pattern}")], run=_run)
        """))
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    pairs = registry.telegram_bots()
    # specific pattern must come before catch-all
    assert pairs[-1][1].pattern == ".*"
