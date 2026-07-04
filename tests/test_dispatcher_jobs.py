import asyncio
import json
from unittest.mock import MagicMock

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
