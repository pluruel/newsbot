from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from newsparser.bots.core.context import Context


@dataclass
class Cron:
    schedule: str
    tz: str = "Asia/Seoul"


@dataclass
class TelegramMatch:
    pattern: str


Trigger = Cron | TelegramMatch


@dataclass
class Bot:
    name: str
    triggers: list[Trigger]
    run: Callable[["Context"], Awaitable[None]]
    enabled: bool = True
    # Long-running bots run as background jobs (JobManager) instead of inline,
    # so the dispatcher stays responsive while they work.
    background: bool = False
