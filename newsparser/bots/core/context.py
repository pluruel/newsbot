from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


_SEND_RETRIES = 3


@dataclass
class TelegramSender:
    bot: Any = None
    chat_id: str | None = None
    alert_chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_ALERT_CHAT_ID", ""))

    async def send(self, text: str) -> None:
        from telegram.error import NetworkError

        target = self.chat_id or self.alert_chat_id
        if not (self.bot and target):
            return
        for attempt in range(1, _SEND_RETRIES + 1):
            try:
                await self.bot.send_message(chat_id=target, text=text[:4096])
                return
            except NetworkError:
                if attempt == _SEND_RETRIES:
                    raise
                logging.getLogger(__name__).warning(
                    "send_message failed (attempt %d/%d), retrying", attempt, _SEND_RETRIES)
                await asyncio.sleep(2 * attempt)


@dataclass
class Context:
    bot_name: str
    workspace: Path
    telegram: TelegramSender
    message: Any = None  # telegram.Message | None

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f"newsparser.bots.{self.bot_name}")

    async def run_in_thread(self, fn: Callable, *args, **kwargs) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)
