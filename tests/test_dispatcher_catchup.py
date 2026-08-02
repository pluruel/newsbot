import asyncio
import json
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import newsparser.dispatcher as dispatcher
from newsparser.bots.core import cron_state
from newsparser.bots.core.jobs import JobManager
from newsparser.bots.core.types import Bot, Cron

KST = ZoneInfo("Asia/Seoul")


class _Registry:
    def __init__(self, pairs):
        self._pairs = pairs

    def cron_bots(self):
        return list(self._pairs)

    def all(self):
        return [bot for bot, _ in self._pairs]


def _ts(y, m, d, hh, mm) -> float:
    return datetime(y, m, d, hh, mm, tzinfo=KST).timestamp()


async def _noop(ctx):
    pass


def _wire(monkeypatch, tmp_path, trigger, last_run=None, now=None):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    bot = Bot(name="market_daily", triggers=[trigger], run=_noop)
    monkeypatch.setattr(dispatcher, "registry", _Registry([(bot, trigger)]))
    if last_run is not None:
        cron_state.record_run("market_daily", when=last_run)
    if now is not None:
        monkeypatch.setattr(cron_state.time, "time", lambda: now)
    app = MagicMock()
    return bot, app


def test_catchup_scheduled_when_fire_was_missed(tmp_path, monkeypatch):
    trigger = Cron("30 7 * * *", tz="Asia/Seoul", catchup=True)
    _, app = _wire(monkeypatch, tmp_path, trigger,
                   last_run=_ts(2026, 7, 30, 7, 30), now=_ts(2026, 8, 2, 9, 0))

    dispatcher._register_catchup_jobs(app)

    app.job_queue.run_once.assert_called_once()
    assert app.job_queue.run_once.call_args.kwargs["name"] == "market_daily-catchup"


def test_no_catchup_when_already_ran_today(tmp_path, monkeypatch):
    trigger = Cron("30 7 * * *", tz="Asia/Seoul", catchup=True)
    _, app = _wire(monkeypatch, tmp_path, trigger,
                   last_run=_ts(2026, 8, 2, 7, 30), now=_ts(2026, 8, 2, 9, 0))

    dispatcher._register_catchup_jobs(app)

    app.job_queue.run_once.assert_not_called()


def test_no_catchup_when_flag_is_off(tmp_path, monkeypatch):
    """Expensive bots (cycle/weekly) must not fire on every restart."""
    trigger = Cron("30 7 * * *", tz="Asia/Seoul")  # catchup defaults to False
    _, app = _wire(monkeypatch, tmp_path, trigger,
                   last_run=_ts(2026, 7, 30, 7, 30), now=_ts(2026, 8, 2, 9, 0))

    dispatcher._register_catchup_jobs(app)

    app.job_queue.run_once.assert_not_called()


def test_catchup_scheduled_when_never_run(tmp_path, monkeypatch):
    trigger = Cron("30 7 * * *", tz="Asia/Seoul", catchup=True)
    _, app = _wire(monkeypatch, tmp_path, trigger, now=_ts(2026, 8, 2, 9, 0))

    dispatcher._register_catchup_jobs(app)

    app.job_queue.run_once.assert_called_once()


async def test_catchup_run_records_last_run(tmp_path, monkeypatch):
    """The catch-up run itself must update the mark, or every later restart
    would re-fire it."""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    ran = asyncio.Event()

    async def run(ctx):
        ran.set()

    bot = Bot(name="market_daily", triggers=[], run=run)
    jm = JobManager(tmp_path)
    monkeypatch.setattr(dispatcher, "jobs", jm)

    cb = dispatcher._make_cron_callback(bot, trigger_kind="catchup")
    await cb(MagicMock())
    await asyncio.wait_for(ran.wait(), 5)
    for _ in range(100):
        if jm.running_for("market_daily") is None:
            break
        await asyncio.sleep(0.01)

    assert cron_state.last_run("market_daily") is not None
    state = json.loads((tmp_path / "jobs.json").read_text())
    assert state["recent"][0]["trigger"] == "catchup"


async def test_failed_run_does_not_record_last_run(tmp_path, monkeypatch):
    """A failed fetch must stay 'missed' so the next restart retries it."""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))

    async def run(ctx):
        raise RuntimeError("yfinance down")

    bot = Bot(name="market_daily", triggers=[], run=run)
    jm = JobManager(tmp_path)
    monkeypatch.setattr(dispatcher, "jobs", jm)

    cb = dispatcher._make_cron_callback(bot, trigger_kind="catchup")
    await cb(MagicMock())
    for _ in range(100):
        if jm.running_for("market_daily") is None:
            break
        await asyncio.sleep(0.01)

    assert cron_state.last_run("market_daily") is None
