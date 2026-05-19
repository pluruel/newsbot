from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class TelegramSender:
    bot: Any = None
    chat_id: str | None = None
    alert_chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_ALERT_CHAT_ID", ""))

    async def send(self, text: str) -> None:
        target = self.chat_id or self.alert_chat_id
        if self.bot and target:
            await self.bot.send_message(chat_id=target, text=text[:4096])


@dataclass
class Context:
    bot_name: str
    workspace: Path
    telegram: TelegramSender
    message: Any = None  # telegram.Message | None

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f"newsparser.bots.{self.bot_name}")

    async def claude(self, prompt: str, **kwargs) -> str:
        from newsparser.bots.core.cost_db import record_run
        from newsparser.claude.runner import run_claude
        try:
            result = await run_claude(prompt, **kwargs)
            meta = {
                "cost_usd": result.cost_usd,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "duration_ms": result.duration_ms,
            }
            await asyncio.to_thread(record_run, bot=self.bot_name, meta=meta, ok=True)
            return result.text
        except Exception as exc:
            await asyncio.to_thread(record_run, bot=self.bot_name, meta={}, ok=False, error=str(exc))
            raise

    async def run_in_thread(self, fn: Callable, *args, **kwargs) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)
