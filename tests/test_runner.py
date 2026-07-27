import json
import threading
import time

import pytest

from newsparser.claude import runner
from newsparser.claude.runner import run_claude, ClaudeError, ClaudeKilled


def _fake_claude(tmp_path, monkeypatch, body: str):
    """Install an executable python script as the claude binary."""
    script = tmp_path / "fake_claude.py"
    script.write_text("#!/usr/bin/env python3\n" + body)
    script.chmod(0o755)
    monkeypatch.setenv("CLAUDE_BIN", str(script))
    return script


_SUCCESS_BODY = """
import json
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "WebSearch"}]}}))
print(json.dumps({
    "type": "result", "subtype": "success", "result": "analysis output",
    "duration_ms": 5000, "total_cost_usd": 0.002,
    "usage": {"input_tokens": 100, "output_tokens": 50},
}))
"""

# Echoes its own argv back as the result text, so tests can assert on flags.
_ARGV_BODY = """
import json, sys
print(json.dumps({"type": "result", "subtype": "success",
                  "result": json.dumps(sys.argv[1:])}))
"""


def test_run_claude_returns_result_text(tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch, _SUCCESS_BODY)
    assert run_claude("/cycle") == "analysis output"


def test_run_claude_raises_on_nonzero_exit(tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch,
                 "import sys\nsys.stderr.write('error message')\nsys.exit(1)\n")
    with pytest.raises(ClaudeError, match="error message"):
        run_claude("/cycle")


def test_run_claude_nonzero_exit_includes_stdout_diagnostics(tmp_path, monkeypatch):
    """When claude crashes with an empty stderr but a traceback on stdout (e.g. a
    skill/tool crash), that stdout tail is the only clue and must not be lost."""
    _fake_claude(tmp_path, monkeypatch,
                 "import sys\nprint('Traceback: skill exploded')\nsys.exit(1)\n")
    with pytest.raises(ClaudeError, match="skill exploded"):
        run_claude("/cycle")


def test_run_claude_raises_when_no_result_event(tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch, "print('not json')\n")
    with pytest.raises(ClaudeError, match="no result event"):
        run_claude("/cycle")


def test_run_claude_raises_on_error_result(tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch, """
import json
print(json.dumps({"type": "result", "subtype": "error_max_turns",
                  "is_error": True, "result": "ran out of turns"}))
""")
    with pytest.raises(ClaudeError, match="error_max_turns"):
        run_claude("/cycle")


def _argv_of(output: str) -> list[str]:
    return json.loads(output)


def test_run_claude_passes_prompt_and_stream_flags(tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch, _ARGV_BODY)
    argv = _argv_of(run_claude("/cycle with context"))
    assert "/cycle with context" in argv
    assert "stream-json" in argv
    assert "--verbose" in argv


def test_run_claude_includes_model_flag(tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch, _ARGV_BODY)
    argv = _argv_of(run_claude("query"))
    assert "--model" in argv
    assert "claude-sonnet-5" in argv


def test_run_claude_includes_mcp_config_when_given(tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch, _ARGV_BODY)
    argv = _argv_of(run_claude("query", mcp_config="mcp.json"))
    assert argv[argv.index("--mcp-config") + 1] == "mcp.json"


def test_run_claude_omits_mcp_config_by_default(tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch, _ARGV_BODY)
    argv = _argv_of(run_claude("query"))
    assert "--mcp-config" not in argv


def test_run_claude_raises_claude_error_on_timeout(tmp_path, monkeypatch):
    """A hung claude must surface as ClaudeError('timed out'), not hang forever —
    callers only catch ClaudeError/RuntimeError/OSError to fail safe."""
    _fake_claude(tmp_path, monkeypatch, "import time\ntime.sleep(30)\n")
    start = time.monotonic()
    with pytest.raises(ClaudeError, match="timed out after 1s"):
        run_claude("query", timeout=1)
    assert time.monotonic() - start < 10


def test_active_runs_tagged_with_job_and_kill_job(tmp_path, monkeypatch):
    """A run started under CURRENT_JOB shows up in active_runs() with that job id,
    and kill_job() terminates its subprocess, surfacing as ClaudeKilled — the
    marker type run_cycle re-raises to abort the remaining categories."""
    _fake_claude(tmp_path, monkeypatch, "import time\ntime.sleep(30)\n")
    errors: list[Exception] = []

    def target():
        runner.CURRENT_JOB.set(7)
        try:
            run_claude("query", timeout=60)
        except ClaudeError as e:
            errors.append(e)

    t = threading.Thread(target=target)
    t.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if any(r["job_id"] == 7 for r in runner.active_runs()):
            break
        time.sleep(0.05)
    else:
        pytest.fail("run never appeared in active_runs()")

    assert runner.kill_job(7) == 1
    t.join(timeout=10)
    assert not t.is_alive()
    assert errors, "killed run must raise ClaudeKilled"
    assert isinstance(errors[0], ClaudeKilled)
    assert not any(r["job_id"] == 7 for r in runner.active_runs())


def test_activity_hook_called_on_events(tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch, _SUCCESS_BODY)
    calls = []
    monkeypatch.setattr(runner, "on_activity", lambda: calls.append(1))
    run_claude("/cycle")
    assert calls


def test_run_claude_permission_mode_defaults_to_deny(tmp_path, monkeypatch):
    """Deny-by-default: a call site that omits permission_mode must get
    --permission-mode default, never fall through to settings.json defaultMode."""
    _fake_claude(tmp_path, monkeypatch, _ARGV_BODY)
    argv = _argv_of(run_claude("query"))
    assert argv[argv.index("--permission-mode") + 1] == "default"
    argv = _argv_of(run_claude("query", permission_mode="bypassPermissions"))
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"


# One error tool_result whose text merely *mentions* Permission denied (an OS
# error, not a tool denial), plus one structured denial in the result event.
_DENIAL_BODY = """
import json
print(json.dumps({"type": "user", "message": {"content": [
    {"type": "tool_result", "is_error": True,
     "content": "bash: /root/x: Permission denied"}]}}))
print(json.dumps({"type": "result", "subtype": "success", "result": "ok",
    "permission_denials": [{"tool_name": "Bash", "tool_use_id": "t1",
                            "tool_input": {"command": "python3 apply_graph.py"}}]}))
"""


def test_denials_use_structured_signal_and_survive_run_end(tmp_path, monkeypatch):
    """Denials come from result.permission_denials (an OS 'Permission denied' in
    tool output must not count) and stay retrievable after the run finishes so
    the JobManager can persist them onto the completed job."""
    _fake_claude(tmp_path, monkeypatch, _DENIAL_BODY)
    token = runner.CURRENT_JOB.set(11)
    try:
        assert run_claude("query") == "ok"
    finally:
        runner.CURRENT_JOB.reset(token)
    denials = runner.consume_job_denials(11)
    assert len(denials) == 1
    assert denials[0].startswith("Bash ")
    assert "python3 apply_graph.py" in denials[0]
    assert runner.consume_job_denials(11) == []  # popped — no leak


def test_describe_event_reports_tool_use():
    event = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "WebSearch"},
        {"type": "tool_use", "name": "Read"},
    ]}}
    assert runner._describe_event(event) == "tool: WebSearch, Read"
    assert runner._describe_event({"type": "user"}) is None
