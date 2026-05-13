from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from newsparser.bots.core.types import Bot, Cron, TelegramMatch

_DEFAULT_BOTS_DIR = Path(__file__).parent.parent  # newsparser/bots/


class BotRegistry:
    def __init__(self, bots_dir: Path | None = None) -> None:
        self._bots_dir = bots_dir or _DEFAULT_BOTS_DIR
        self._bots: list[Bot] = []

    def load(self) -> None:
        self._bots = []
        for bot_file in sorted(self._bots_dir.glob("*/bot.py")):
            mod_name = f"_bots_dynamic.{bot_file.parent.name}"
            sys.modules.pop(mod_name, None)
            spec = importlib.util.spec_from_file_location(mod_name, bot_file)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error("Failed to load %s: %s", bot_file, exc)
                continue
            bot = getattr(mod, "BOT", None)
            if isinstance(bot, Bot) and bot.enabled:
                self._bots.append(bot)

    def all(self) -> list[Bot]:
        return list(self._bots)

    def names(self) -> list[str]:
        return [b.name for b in self._bots]

    def cron_bots(self) -> list[tuple[Bot, Cron]]:
        return [
            (bot, t)
            for bot in self._bots
            for t in bot.triggers
            if isinstance(t, Cron)
        ]

    def telegram_bots(self) -> list[tuple[Bot, TelegramMatch]]:
        pairs = [
            (bot, t)
            for bot in self._bots
            for t in bot.triggers
            if isinstance(t, TelegramMatch)
        ]
        # Ensure catch-all patterns sort last so specific patterns match first.
        # Without this, 'tracker' (t) would beat 'weekly' (w) alphabetically.
        pairs.sort(key=lambda pair: pair[1].pattern == ".*")
        return pairs
