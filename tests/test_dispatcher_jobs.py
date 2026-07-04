import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import newsparser.dispatcher as dispatcher
from newsparser.bots.core.jobs import JobManager
from newsparser.bots.core.types import Bot


class _Registry:
    def __init__(self, bots):
        self._bots = bots

    def all(self):
        return list(self._bots)


async def test_poll_job_requests_starts_requested_bot(tmp_path, monkeypatch):
    ran = asyncio.Event()

    async def run(ctx):
        ran.set()

    bot = Bot(name="cycle", triggers=[], run=run, background=True)
    jm = JobManager(tmp_path)
    monkeypatch.setattr(dispatcher, "jobs", jm)
    monkeypatch.setattr(dispatcher, "registry", _Registry([bot]))

    req_dir = tmp_path / "job-requests"
    req_dir.mkdir()
    (req_dir / "r.json").write_text(json.dumps({"bot": "cycle", "chat_id": "123"}))

    ptb_ctx = MagicMock()
    await dispatcher._poll_job_requests(ptb_ctx)
    await asyncio.wait_for(ran.wait(), 5)
    # Wait for the job task to finish so state lands in `recent`.
    for _ in range(100):
        if jm.running_for("cycle") is None:
            break
        await asyncio.sleep(0.01)
    state = json.loads((tmp_path / "jobs.json").read_text())
    assert state["recent"][0]["bot"] == "cycle"
    assert state["recent"][0]["trigger"] == "mcp"


async def test_poll_job_requests_ignores_unknown_bot(tmp_path, monkeypatch):
    jm = JobManager(tmp_path)
    monkeypatch.setattr(dispatcher, "jobs", jm)
    monkeypatch.setattr(dispatcher, "registry", _Registry([]))

    req_dir = tmp_path / "job-requests"
    req_dir.mkdir()
    (req_dir / "r.json").write_text(json.dumps({"bot": "nope"}))

    await dispatcher._poll_job_requests(MagicMock())
    state = json.loads((tmp_path / "jobs.json").read_text())
    assert state["running"] == []


async def test_poll_job_requests_processes_kill_requests(tmp_path, monkeypatch):
    """The poll is the only consumer of jobs.kill markers — it must invoke the
    in-process kill on every tick."""
    jm = JobManager(tmp_path)
    called = []
    monkeypatch.setattr(jm, "process_kill_requests", lambda: called.append(1))
    monkeypatch.setattr(dispatcher, "jobs", jm)
    monkeypatch.setattr(dispatcher, "registry", _Registry([]))

    await dispatcher._poll_job_requests(MagicMock())
    assert called == [1]


async def test_cron_skip_notifies_telegram(tmp_path, monkeypatch):
    """A hung job silently eating cron ticks must at least alert the user."""
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "999")
    release = asyncio.Event()

    async def run(ctx):
        await release.wait()

    bot = Bot(name="cycle", triggers=[], run=run, background=True)
    jm = JobManager(tmp_path)
    monkeypatch.setattr(dispatcher, "jobs", jm)

    ptb_ctx = MagicMock()
    ptb_ctx.bot.send_message = AsyncMock()
    cb = dispatcher._make_cron_callback(bot)
    await cb(ptb_ctx)                 # starts the job
    ptb_ctx.bot.send_message.reset_mock()
    await cb(ptb_ctx)                 # skipped — must warn
    assert ptb_ctx.bot.send_message.called
    text = ptb_ctx.bot.send_message.call_args.kwargs["text"]
    assert "⚠️" in text
    assert "cycle" in text

    release.set()
    await jm.running_for("cycle").task
