from unittest.mock import patch, MagicMock
import json
import subprocess
from newsparser.claude.runner import run_claude, run_claude_json, ClaudeError

def test_run_claude_returns_stdout():
    mock_result = MagicMock(returncode=0, stdout="analysis output", stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result):
        output = run_claude("/cycle")
    assert output == "analysis output"

def test_run_claude_raises_on_nonzero_exit():
    mock_result = MagicMock(returncode=1, stdout="", stderr="error message")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result):
        try:
            run_claude("/cycle")
            assert False, "should have raised"
        except ClaudeError as e:
            assert "error message" in str(e)

def test_run_claude_passes_prompt():
    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result) as mock_run:
        run_claude("/cycle with context")
    args = mock_run.call_args[0][0]
    assert "/cycle with context" in " ".join(args)

def test_run_claude_includes_model_flag():
    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result) as mock_run:
        run_claude("query")
    cmd = mock_run.call_args[0][0]
    assert "--model" in cmd
    assert "claude-sonnet-5" in cmd

def test_run_claude_includes_mcp_config_when_given():
    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result) as mock_run:
        run_claude("query", mcp_config="mcp.json")
    cmd = mock_run.call_args[0][0]
    assert "--mcp-config" in cmd
    idx = cmd.index("--mcp-config")
    assert cmd[idx + 1] == "mcp.json"

def test_run_claude_omits_mcp_config_by_default():
    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result) as mock_run:
        run_claude("query")
    cmd = mock_run.call_args[0][0]
    assert "--mcp-config" not in cmd

def test_run_claude_raises_claude_error_on_timeout():
    """subprocess.TimeoutExpired must surface as ClaudeError, not raw —
    callers only catch ClaudeError/RuntimeError/OSError to fail safe."""
    with patch("newsparser.claude.runner.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30)):
        try:
            run_claude("query", timeout=30)
            assert False, "should have raised"
        except ClaudeError as e:
            assert "30" in str(e)

def test_run_claude_json_raises_claude_error_on_timeout():
    with patch("newsparser.claude.runner.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30)):
        try:
            run_claude_json("query", timeout=30)
            assert False, "should have raised"
        except ClaudeError as e:
            assert "30" in str(e)

def test_run_claude_json_returns_text_and_meta():
    from unittest.mock import MagicMock, patch
    payload = json.dumps({
        "result": "analysis",
        "duration_ms": 5000,
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "cost_usd": 0.002,
    })
    mock_result = MagicMock(returncode=0, stdout=payload, stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result):
        from newsparser.claude.runner import run_claude_json
        text, meta = run_claude_json("/cycle")
    assert text == "analysis"
    assert meta["duration_ms"] == 5000
    assert meta["input_tokens"] == 100
    assert meta["cost_usd"] == 0.002
