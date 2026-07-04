import asyncio
import json

import pytest

from newsparser.bots.core.context import Context, TelegramSender
from newsparser.bots.core.jobs import JobManager
from newsparser.bots.core.types import Bot
from newsparser.claude import runner


class _FakeTelegram(TelegramSender):
    def __init__(self):
        super().__init__(bot=None, chat_id=None)
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


def _ctx(tmp_path, name="cycle"):
    return Context(bot_name=name, workspace=tmp_path, telegram=_FakeTelegram())


def _state(tmp_path) -> dict:
    return json.loads((tmp_path / "jobs.json").read_text())


async def test_start_runs_bot_and_persists_lifecycle(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    async def run(ctx):
        started.set()
        await release.wait()

    bot = Bot(name="cycle", triggers=[], run=run, background=True)
    jm = JobManager(tmp_path)
    job = jm.start(bot, _ctx(tmp_path), trigger="telegram")
    assert job is not None
    await asyncio.wait_for(started.wait(), 5)

    state = _state(tmp_path)
    assert state["running"][0]["bot"] == "cycle"
    assert state["running"][0]["status"] == "running"
    assert state["running"][0]["trigger"] == "telegram"

    release.set()
    await job.task
    state = _state(tmp_path)
    assert state["running"] == []
    assert state["recent"][0]["bot"] == "cycle"
    assert state["recent"][0]["status"] == "done"


async def test_duplicate_bot_is_rejected(tmp_path):
    release = asyncio.Event()

    async def run(ctx):
        await release.wait()

    bot = Bot(name="cycle", triggers=[], run=run, background=True)
    jm = JobManager(tmp_path)
    ctx = _ctx(tmp_path)
    job = jm.start(bot, ctx, trigger="cron")
    assert job is not None
    assert jm.start(bot, ctx, trigger="telegram") is None
    # A different bot may still start.
    other = Bot(name="weekly", triggers=[], run=run, background=True)
    job2 = jm.start(other, _ctx(tmp_path, "weekly"), trigger="cron")
    assert job2 is not None
    release.set()
    await asyncio.gather(job.task, job2.task)


async def test_failure_marks_failed_and_notifies(tmp_path):
    async def run(ctx):
        raise RuntimeError("boom")

    bot = Bot(name="cycle", triggers=[], run=run, background=True)
    jm = JobManager(tmp_path)
    ctx = _ctx(tmp_path)
    job = jm.start(bot, ctx, trigger="cron")
    await job.task
    state = _state(tmp_path)
    assert state["recent"][0]["status"] == "failed"
    assert "boom" in state["recent"][0]["error"]
    assert any("❌" in m for m in ctx.telegram.sent)


async def test_kill_request_marks_killed(tmp_path):
    """When the MCP kill_job tool killed the subprocess (and left a marker in
    jobs.kill), the resulting exception must be reported as 🛑 중단, not ❌ 실패."""
    async def run(ctx):
        raise RuntimeError("claude exited -9")

    bot = Bot(name="cycle", triggers=[], run=run, background=True)
    jm = JobManager(tmp_path)
    ctx = _ctx(tmp_path)
    job = jm.start(bot, ctx, trigger="telegram")
    (tmp_path / "jobs.kill").write_text(json.dumps([job.id]))
    await job.task
    state = _state(tmp_path)
    assert state["recent"][0]["status"] == "killed"
    assert any("🛑" in m for m in ctx.telegram.sent)
    assert not (tmp_path / "jobs.kill").exists()


async def test_consume_requests_reads_and_deletes(tmp_path):
    jm = JobManager(tmp_path)
    req_dir = tmp_path / "job-requests"
    req_dir.mkdir()
    (req_dir / "a.json").write_text(json.dumps({"bot": "cycle", "chat_id": "123"}))
    (req_dir / "b.json").write_text("not json")
    (req_dir / "c.json").write_text(json.dumps({"no_bot": True}))
    reqs = jm.consume_requests()
    assert reqs == [{"bot": "cycle", "chat_id": "123"}]
    assert list(req_dir.glob("*.json")) == []
    assert jm.consume_requests() == []


async def test_init_clears_stale_state(tmp_path):
    (tmp_path / "jobs.json").write_text(json.dumps(
        {"running": [{"id": 1, "bot": "cycle"}], "recent": []}))
    JobManager(tmp_path)
    assert _state(tmp_path)["running"] == []


async def test_init_clears_stale_kill_marker(tmp_path):
    """Job ids restart at 1 per process, so a marker surviving a restart would
    relabel an unrelated new job's failure as 🛑."""
    (tmp_path / "jobs.kill").write_text("[2]")
    JobManager(tmp_path)
    assert not (tmp_path / "jobs.kill").exists()


async def test_process_kill_requests_kills_running_and_prunes_stale(tmp_path, monkeypatch):
    release = asyncio.Event()

    async def run(ctx):
        await release.wait()

    bot = Bot(name="cycle", triggers=[], run=run, background=True)
    jm = JobManager(tmp_path)
    job = jm.start(bot, _ctx(tmp_path), trigger="telegram")

    killed: list[int] = []
    monkeypatch.setattr(runner, "kill_job", lambda jid: (killed.append(jid), 1)[1])
    (tmp_path / "jobs.kill").write_text(json.dumps([job.id, 99]))

    jm.process_kill_requests()
    assert killed == [job.id]
    # The live id stays for _run to consume (🛑 labelling); the stale 99 is pruned.
    assert json.loads((tmp_path / "jobs.kill").read_text()) == [job.id]

    release.set()
    await job.task


async def test_cancelled_job_not_left_running(tmp_path):
    """Dispatcher shutdown cancels job tasks; the job must land in `recent` as
    cancelled, not linger with status='running'."""
    started = asyncio.Event()

    async def run(ctx):
        started.set()
        await asyncio.Event().wait()

    bot = Bot(name="cycle", triggers=[], run=run, background=True)
    jm = JobManager(tmp_path)
    job = jm.start(bot, _ctx(tmp_path), trigger="cron")
    await asyncio.wait_for(started.wait(), 5)

    job.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job.task
    state = _state(tmp_path)
    assert state["running"] == []
    assert state["recent"][0]["status"] == "cancelled"


async def test_persisted_state_readable_by_mcp_job_status(tmp_path, monkeypatch):
    """Producer/consumer contract: the file persist() writes must be readable by
    the MCP job_status tool (separate process in prod, same file format)."""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    from newsparser.mcp_server import job_status

    started = asyncio.Event()
    release = asyncio.Event()

    async def run(ctx):
        started.set()
        await release.wait()

    bot = Bot(name="cycle", triggers=[], run=run, background=True)
    jm = JobManager(tmp_path)
    job = jm.start(bot, _ctx(tmp_path), trigger="cron")
    await asyncio.wait_for(started.wait(), 5)
    out = job_status()
    assert f"#{job.id} cycle" in out
    assert "실행 중" in out

    release.set()
    await job.task
    out = job_status()
    assert "done" in out
