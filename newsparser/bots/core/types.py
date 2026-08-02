from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from newsparser.bots.core.context import Context


@dataclass
class Cron:
    schedule: str
    tz: str = "Asia/Seoul"
    # Run once at dispatcher startup if this trigger came due while the process
    # was down — APScheduler has no persistent job store, so an unfired trigger
    # is otherwise lost until the next tick. Only for cheap, idempotent bots:
    # it fires on every restart that spans a scheduled time.
    catchup: bool = False


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
