from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from newsparser.bots.core.context import Context
from newsparser.bots.core.types import Bot
from newsparser.claude import runner

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

STATE_FILE = "jobs.json"
KILL_FILE = "jobs.kill"
_RECENT_MAX = 10


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, _KST).isoformat(timespec="seconds")


@dataclass
class Job:
    id: int
    bot_name: str
    trigger: str  # "cron" | "telegram"
    started_at: float
    status: str = "running"  # running | done | failed | killed
    finished_at: float | None = None
    error: str | None = None
    task: asyncio.Task | None = None


class JobManager:
    """Runs long bots as background asyncio tasks and mirrors their state to
    workspace/jobs.json so other processes (the tracker's MCP server) can read
    it. One job per bot at a time."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._lock = threading.Lock()
        self._running: dict[int, Job] = {}
        self._recent: list[Job] = []
        self._next_id = 1
        # Heartbeat: re-persist whenever an active claude run makes progress,
        # so "last activity" in jobs.json stays fresh. Called from worker threads.
        runner.on_activity = self.persist
        self.persist()  # clear stale state from a previous process

    def running_for(self, bot_name: str) -> Job | None:
        with self._lock:
            for job in self._running.values():
                if job.bot_name == bot_name:
                    return job
        return None

    def start(self, bot: Bot, ctx: Context, trigger: str) -> Job | None:
        """Launch `bot` as a background job. Returns None if one is already running."""
        if self.running_for(bot.name) is not None:
            return None
        with self._lock:
            job = Job(id=self._next_id, bot_name=bot.name, trigger=trigger,
                      started_at=time.time())
            self._next_id += 1
            self._running[job.id] = job
        job.task = asyncio.create_task(self._run(bot, ctx, job),
                                       name=f"job-{job.id}-{bot.name}")
        self.persist()
        return job

    async def _run(self, bot: Bot, ctx: Context, job: Job) -> None:
        token = runner.CURRENT_JOB.set(job.id)
        try:
            await bot.run(ctx)
            job.status = "done"
        except Exception as exc:
            if self._consume_kill_request(job.id):
                job.status = "killed"
                logger.warning("Job #%s (%s) killed by request", job.id, job.bot_name)
                await self._notify(ctx, f"🛑 {bot.name} 작업 중단됨 (#{job.id})")
            else:
                job.status = "failed"
                job.error = str(exc)[:500]
                tb = traceback.format_exc()
                logger.exception("Job #%s (%s) failed", job.id, job.bot_name)
                await self._notify(ctx, f"❌ {bot.name} 실패\n{tb[-1500:]}")
        finally:
            runner.CURRENT_JOB.reset(token)
            job.finished_at = time.time()
            with self._lock:
                self._running.pop(job.id, None)
                self._recent.insert(0, job)
                del self._recent[_RECENT_MAX:]
            self.persist()

    @staticmethod
    async def _notify(ctx: Context, text: str) -> None:
        try:
            await ctx.telegram.send(text)
        except Exception:
            pass

    def _consume_kill_request(self, job_id: int) -> bool:
        """True if `job_id` is in the kill-request file (written by the MCP
        kill_job tool); removes it from the file."""
        path = self._workspace / KILL_FILE
        try:
            ids = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        if job_id not in ids:
            return False
        remaining = [i for i in ids if i != job_id]
        try:
            if remaining:
                path.write_text(json.dumps(remaining))
            else:
                path.unlink()
        except OSError:
            pass
        return True

    def _job_dict(self, job: Job, activity: dict | None) -> dict:
        d = {
            "id": job.id,
            "bot": job.bot_name,
            "trigger": job.trigger,
            "status": job.status,
            "started_at": _iso(job.started_at),
            "elapsed_s": int((job.finished_at or time.time()) - job.started_at),
        }
        if job.finished_at is not None:
            d["finished_at"] = _iso(job.finished_at)
        if job.error:
            d["error"] = job.error
        if activity:
            d["activity"] = {
                "desc": activity["activity"],
                "turns": activity["turns"],
                "last_event_at": _iso(activity["last_event_at"]),
                "idle_s": int(time.time() - activity["last_event_at"]),
                "pid": activity["pid"],
            }
        return d

    def persist(self) -> None:
        """Atomically rewrite workspace/jobs.json. Thread-safe — called from the
        event loop and from runner worker threads."""
        by_job: dict[int, dict] = {}
        for r in runner.active_runs():
            if r["job_id"] is None:
                continue
            prev = by_job.get(r["job_id"])
            if prev is None or r["last_event_at"] > prev["last_event_at"]:
                by_job[r["job_id"]] = r
        with self._lock:
            state = {
                "updated_at": _iso(time.time()),
                "running": [self._job_dict(j, by_job.get(j.id))
                            for j in self._running.values()],
                "recent": [self._job_dict(j, None) for j in self._recent],
            }
            tmp = self._workspace / (STATE_FILE + ".tmp")
            try:
                self._workspace.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
                os.replace(tmp, self._workspace / STATE_FILE)
            except OSError:
                logger.exception("Failed to persist job state")
