import itertools
import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from contextvars import ContextVar
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Project root: two levels above this file (newsparser/claude/runner.py → project root)
_PROJECT_ROOT = Path(__file__).parent.parent.parent


class ClaudeError(RuntimeError):
    pass


class ClaudeKilled(ClaudeError):
    """The subprocess was terminated by kill_job() — an intentional user kill,
    not a failure. Scripts that swallow per-step errors must re-raise this so
    the JobManager can stop the job and report 🛑."""


# Job id set by the JobManager before a bot runs. asyncio.to_thread copies the
# context, so worker threads (and every run_claude call inside them) inherit it —
# that is how an active claude subprocess gets correlated back to its job.
CURRENT_JOB: ContextVar[int | None] = ContextVar("newsparser_current_job", default=None)

# Optional hook (set by the JobManager) invoked — throttled — whenever an active
# run makes progress, so job state can be re-persisted with fresh activity info.
on_activity: Callable[[], None] | None = None
# Each hook call rewrites jobs.json in full; consumers recompute idle/elapsed
# from last_event_at at read time, so the heartbeat only needs to keep the
# activity description roughly fresh.
_NOTIFY_INTERVAL = 30.0


# Resolve at call time. Override with `CLAUDE_BIN` env var when PATH lookup fails
# (e.g. systemd services with minimal PATH).
def _claude_bin() -> str:
    return os.environ.get("CLAUDE_BIN", "claude")


class _Run:
    __slots__ = ("run_id", "job_id", "pid", "prompt_head", "started_at",
                 "last_event_at", "activity", "turns", "proc", "killed",
                 "denials", "_last_notify")

    def __init__(self, run_id: int, job_id: int | None, proc: subprocess.Popen, prompt: str):
        self.run_id = run_id
        self.job_id = job_id
        self.pid = proc.pid
        self.proc = proc
        self.prompt_head = prompt[:80]
        self.started_at = time.time()
        self.last_event_at = self.started_at
        self.activity = "시작 대기"
        self.turns = 0
        self.killed = False
        self.denials: list[str] = []
        self._last_notify = 0.0


_ACTIVE: dict[int, _Run] = {}
_ACTIVE_LOCK = threading.Lock()
_RUN_IDS = itertools.count(1)

# Denials from finished runs, keyed by job id, so they outlive the _Run and the
# JobManager can attach them to the completed Job (jobs.json "recent" audit).
_JOB_DENIALS: dict[int, list[str]] = {}
_MAX_JOB_DENIALS = 50


def job_denials(job_id: int) -> list[str]:
    """Denials accumulated so far by `job_id`'s finished runs (non-destructive)."""
    with _ACTIVE_LOCK:
        return list(_JOB_DENIALS.get(job_id, ()))


def consume_job_denials(job_id: int) -> list[str]:
    """Pop and return every denial recorded for `job_id` — called by the
    JobManager when the job finishes, so the entry can't leak."""
    with _ACTIVE_LOCK:
        return _JOB_DENIALS.pop(job_id, [])


def active_runs() -> list[dict]:
    """Snapshot of in-flight claude subprocesses (for job status reporting)."""
    with _ACTIVE_LOCK:
        runs = list(_ACTIVE.values())
    return [
        {
            "run_id": r.run_id,
            "job_id": r.job_id,
            "pid": r.pid,
            "prompt_head": r.prompt_head,
            "started_at": r.started_at,
            "last_event_at": r.last_event_at,
            "activity": r.activity,
            "turns": r.turns,
            "denials": list(r.denials),
        }
        for r in runs
    ]


def kill_job(job_id: int) -> int:
    """Kill every active claude subprocess tagged with `job_id`. Returns count killed.
    The killed runs raise ClaudeKilled (not a generic ClaudeError) so callers can
    distinguish an intentional kill from a crash."""
    with _ACTIVE_LOCK:
        runs = [r for r in _ACTIVE.values() if r.job_id == job_id]
    for run in runs:
        run.killed = True
        try:
            run.proc.kill()
        except OSError:
            pass
    return len(runs)


# Headless runs auto-deny tools outside the allowlist; the model just sees an
# error tool_result and moves on. The CLI reports every auto-denied call
# structurally in the final result event's `permission_denials` — use that
# (not error-text matching, which false-positives on OS "Permission denied",
# PermissionError tracebacks, git failures, and breaks silently if the CLI
# wording changes) so a whitelist gap surfaces in logs/jobs.json instead of
# silently degrading output quality.
def _extract_denials(event: dict) -> list[str]:
    if event.get("type") != "result":
        return []
    denials: list[str] = []
    for d in event.get("permission_denials") or []:
        if not isinstance(d, dict):
            continue
        name = d.get("tool_name", "?")
        args = json.dumps(d.get("tool_input", {}), ensure_ascii=False)
        denials.append(f"{name} {args}"[:200])
    return denials


def _describe_event(event: dict) -> str | None:
    if event.get("type") != "assistant":
        return None
    blocks = (event.get("message") or {}).get("content") or []
    tools = [b.get("name") for b in blocks
             if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")]
    if tools:
        return "tool: " + ", ".join(tools)
    return "응답 작성"


def _note_event(run: _Run, event: dict) -> None:
    run.last_event_at = time.time()
    if event.get("type") == "assistant":
        run.turns += 1
    for text in _extract_denials(event):
        run.denials.append(text)
        logger.warning("Tool call denied (run %d, job %s, prompt %r): %s",
                       run.run_id, run.job_id, run.prompt_head, text)
    desc = _describe_event(event)
    if desc:
        run.activity = desc
    hook = on_activity
    if hook and run.last_event_at - run._last_notify >= _NOTIFY_INTERVAL:
        run._last_notify = run.last_event_at
        try:
            hook()
        except Exception:
            pass


def _build_cmd(
    prompt: str,
    mcp_config: str | None,
    model: str,
    system_prompt: str | None,
    allowed_tools: list[str] | None,
    permission_mode: str,
) -> list[str]:
    # stream-json (with -p it requires --verbose) so progress is observable while
    # the run is in flight; the final "result" event carries text + usage/cost.
    cmd = [_claude_bin(), "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--model", model]
    if mcp_config is not None:
        cmd += ["--mcp-config", mcp_config]
    if system_prompt is not None:
        cmd += ["--system-prompt", system_prompt]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    # Always passed explicitly — never fall through to the settings.json
    # defaultMode, so a call site that forgets the kwarg gets deny-by-default,
    # not a silent escalation to whatever the settings file says.
    cmd += ["--permission-mode", permission_mode]
    return cmd


def _run_stream(cmd: list[str], timeout: int, prompt: str) -> dict:
    """Run claude, streaming events. Returns the final result event.
    Raises ClaudeError on failure (including timeout and external kill)."""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=_PROJECT_ROOT,
        )
    except OSError as exc:
        raise ClaudeError(f"failed to start claude: {exc}") from exc

    run = _Run(next(_RUN_IDS), CURRENT_JOB.get(), proc, prompt)
    with _ACTIVE_LOCK:
        _ACTIVE[run.run_id] = run

    stderr_chunks: list[str] = []
    drain = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()), daemon=True)
    drain.start()

    timed_out = threading.Event()

    def _expire() -> None:
        timed_out.set()
        try:
            proc.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout, _expire)
    timer.daemon = True
    timer.start()

    result_event: dict | None = None
    # Non-JSON stdout lines are diagnostics (tracebacks, partial output) — keep a
    # tail so a nonzero exit with an empty stderr still surfaces a clue.
    stdout_tail: deque[str] = deque(maxlen=20)
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                stdout_tail.append(line)
                continue
            _note_event(run, event)
            if event.get("type") == "result":
                result_event = event
        proc.wait()
        drain.join(timeout=5)
    finally:
        timer.cancel()
        with _ACTIVE_LOCK:
            _ACTIVE.pop(run.run_id, None)
            if run.denials and run.job_id is not None:
                bucket = _JOB_DENIALS.setdefault(run.job_id, [])
                bucket.extend(run.denials)
                del bucket[_MAX_JOB_DENIALS:]

    if timed_out.is_set():
        raise ClaudeError(f"claude timed out after {timeout}s")
    if run.killed:
        raise ClaudeKilled(f"claude run killed by kill request (job #{run.job_id})")
    stderr = (stderr_chunks[0] if stderr_chunks else "") or ""
    if proc.returncode != 0:
        detail = f"stderr={stderr[:500]}"
        if stdout_tail:
            detail += f" stdout={' | '.join(stdout_tail)[-500:]}"
        raise ClaudeError(f"claude exited {proc.returncode}: {detail}")
    if result_event is None:
        raise ClaudeError(f"claude produced no result event: stderr={stderr[:300]}")
    if result_event.get("is_error"):
        raise ClaudeError(
            f"claude result error ({result_event.get('subtype')}): "
            f"{str(result_event.get('result', ''))[:300]}"
        )
    return result_event


def run_claude(
    prompt: str,
    timeout: int = 1500,
    mcp_config: str | None = None,
    model: str = "claude-sonnet-5",
    system_prompt: str | None = None,
    allowed_tools: list[str] | None = None,
    permission_mode: str = "default",
) -> str:
    """Invoke claude CLI headless and return the result text. Raises ClaudeError on failure (including timeout).

    permission_mode defaults to "default" (auto-deny outside allowed_tools) —
    trusted-input call sites must opt UP to bypassPermissions explicitly."""
    cmd = _build_cmd(prompt, mcp_config, model, system_prompt, allowed_tools, permission_mode)
    event = _run_stream(cmd, timeout, prompt)
    return event.get("result", "")
