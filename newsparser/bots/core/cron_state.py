"""Durable last-run record for cron bots, so a missed fire can be caught up.

All cron scheduling is APScheduler *inside the dispatcher process* (see
`dispatcher._register_cron_jobs`) with no persistent job store. A trigger that
comes due while the dispatcher is down is therefore never fired and never
replayed — the run is silently skipped until the next day's tick. For
`market_daily` that means a restart spanning 07:30 KST leaves a hole in
`market_daily` that nobody notices until someone queries the gap.

`workspace/cron-state.json` records when each cron bot last completed, which is
what lets `dispatcher._register_catchup_jobs` tell "we missed a fire" apart from
"this bot simply has not come due yet". Opt in per trigger with
``Cron(..., catchup=True)`` — a bot whose run is expensive or non-idempotent
should leave it off.

Best-effort by design: every read/write swallows its errors, because losing the
record costs at most one redundant (idempotent) catch-up run, while raising here
would take down an otherwise healthy job.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger

from newsparser.bots.core.types import Cron
from newsparser.paths import workspace_dir

logger = logging.getLogger(__name__)

STATE_FILE = "cron-state.json"


def _path():
    return workspace_dir() / STATE_FILE


def load() -> dict[str, float]:
    """Bot name -> epoch seconds of its last completed cron run."""
    try:
        data = json.loads(_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, (int, float))}


def last_run(bot_name: str) -> float | None:
    return load().get(bot_name)


def record_run(bot_name: str, when: float | None = None) -> None:
    """Mark `bot_name` as having completed a scheduled run at `when` (now by
    default). Read-modify-write: the dispatcher is the only writer."""
    state = load()
    state[bot_name] = time.time() if when is None else when
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(path)
    except OSError as exc:
        logger.warning("Could not record cron run for %s: %s", bot_name, exc)


def missed_fire(cron: Cron, last: float | None, now: float | None = None) -> bool:
    """True if `cron` had a scheduled fire time in (last, now] — i.e. the
    dispatcher was down (or the bot was not yet registered) when it came due.

    `last is None` means we have no evidence the bot ever ran, which counts as
    missed: the catch-up run is idempotent, so erring toward running is right.
    """
    try:
        trigger = CronTrigger.from_crontab(cron.schedule, timezone=cron.tz)
    except ValueError as exc:
        logger.warning("Bad cron schedule %r: %s", cron.schedule, exc)
        return False
    if last is None:
        return True
    now_dt = datetime.fromtimestamp(time.time() if now is None else now, tz=timezone.utc)
    # +1s so a fire time we already ran at is not re-reported as missed —
    # get_next_fire_time's lower bound is inclusive.
    after_last = datetime.fromtimestamp(last + 1, tz=timezone.utc)
    due = trigger.get_next_fire_time(None, after_last)
    return due is not None and due <= now_dt
