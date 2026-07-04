import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from newsparser.mcp_server import job_status, kill_job


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("WORKSPACE_DIR", str(ws))
    return ws


def _write_state(ws: Path, running=None, recent=None):
    (ws / "jobs.json").write_text(json.dumps({
        "updated_at": "2026-07-04T12:00:00+09:00",
        "running": running or [],
        "recent": recent or [],
    }, ensure_ascii=False))


def test_job_status_without_file(workspace):
    assert "jobs.json" in job_status()


def test_job_status_reports_running_and_recent(workspace):
    _write_state(
        workspace,
        running=[{
            "id": 3, "bot": "cycle", "trigger": "cron", "status": "running",
            "started_at": "2026-07-04T12:00:00+09:00", "elapsed_s": 754,
            "activity": {"desc": "tool: WebSearch", "turns": 24,
                         "last_event_at": "2026-07-04T12:12:30+09:00",
                         "idle_s": 4, "pid": 4242},
        }],
        recent=[{
            "id": 2, "bot": "weekly", "trigger": "telegram", "status": "done",
            "started_at": "2026-07-04T09:00:00+09:00",
            "finished_at": "2026-07-04T09:18:00+09:00", "elapsed_s": 1080,
        }],
    )
    out = job_status()
    assert "#3 cycle" in out
    assert "tool: WebSearch" in out
    assert "12분 34초" in out
    assert "#2 weekly" in out
    assert "done" in out


def test_job_status_running_without_activity(workspace):
    _write_state(workspace, running=[{
        "id": 1, "bot": "cycle", "trigger": "cron", "status": "running",
        "started_at": "2026-07-04T12:00:00+09:00", "elapsed_s": 10,
    }])
    assert "활성 claude 서브프로세스 없음" in job_status()


def test_kill_job_unknown_id(workspace):
    _write_state(workspace)
    assert "없다" in kill_job(99)


def test_kill_job_kills_pid_and_writes_marker(workspace):
    proc = subprocess.Popen(["sleep", "60"])
    try:
        _write_state(workspace, running=[{
            "id": 5, "bot": "cycle", "trigger": "cron", "status": "running",
            "started_at": "2026-07-04T12:00:00+09:00", "elapsed_s": 30,
            "activity": {"desc": "tool: Bash", "turns": 3,
                         "last_event_at": "2026-07-04T12:00:20+09:00",
                         "idle_s": 10, "pid": proc.pid},
        }])
        out = kill_job(5)
        assert "중단 요청 완료" in out
        assert json.loads((workspace / "jobs.kill").read_text()) == [5]
        assert proc.wait(timeout=5) == -signal.SIGKILL
    finally:
        if proc.poll() is None:
            proc.kill()


def test_kill_job_without_active_subprocess_still_marks(workspace):
    _write_state(workspace, running=[{
        "id": 6, "bot": "cycle", "trigger": "cron", "status": "running",
        "started_at": "2026-07-04T12:00:00+09:00", "elapsed_s": 30,
    }])
    out = kill_job(6)
    assert "즉시 중단할 수 없다" in out
    assert json.loads((workspace / "jobs.kill").read_text()) == [6]
