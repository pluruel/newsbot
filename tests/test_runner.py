from unittest.mock import patch, MagicMock
from newsparser.claude.runner import run_claude, ClaudeError

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
    assert "claude-sonnet-4-6" in cmd

def test_run_claude_includes_mcp_config_when_given():
    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result) as mock_run:
        run_claude("query", mcp_config="mcp.json")
    cmd = mock_run.call_args[0][0]
    assert "--mcp-config" in cmd
    assert "mcp.json" in cmd

def test_run_claude_omits_mcp_config_by_default():
    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result) as mock_run:
        run_claude("query")
    cmd = mock_run.call_args[0][0]
    assert "--mcp-config" not in cmd
