import pytest
from unittest.mock import patch, AsyncMock

from newsparser.claude.runner import run_claude, RunResult, ClaudeError
from claude_code_sdk import ResultMessage


def _make_result_message(text: str = "ok", cost: float = 0.001) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=500,
        duration_api_ms=400,
        is_error=False,
        num_turns=1,
        session_id="test-session",
        total_cost_usd=cost,
        usage={"input_tokens": 10, "output_tokens": 5},
        result=text,
    )


async def _mock_sdk_query(text: str = "ok"):
    """Async generator that yields one ResultMessage."""
    yield _make_result_message(text)


async def test_run_claude_returns_text():
    with patch("newsparser.claude.runner._sdk_query", side_effect=lambda **kw: _mock_sdk_query("analysis output")):
        result = await run_claude("/cycle")
    assert isinstance(result, RunResult)
    assert result.text == "analysis output"


async def test_run_claude_returns_cost_and_tokens():
    async def gen(**kw):
        yield _make_result_message("output")

    with patch("newsparser.claude.runner._sdk_query", side_effect=lambda **kw: gen(**kw)):
        result = await run_claude("query")
    assert result.cost_usd == 0.001
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.duration_ms == 500


async def test_run_claude_raises_claude_error_on_sdk_exception():
    async def gen(**kw):
        raise RuntimeError("SDK failure")
        yield  # make it a generator

    with patch("newsparser.claude.runner._sdk_query", side_effect=lambda **kw: gen(**kw)):
        with pytest.raises(ClaudeError, match="SDK failure"):
            await run_claude("/cycle")


async def test_run_claude_raises_on_no_result():
    async def gen(**kw):
        return
        yield  # empty generator

    with patch("newsparser.claude.runner._sdk_query", side_effect=lambda **kw: gen(**kw)):
        with pytest.raises(ClaudeError, match="no result"):
            await run_claude("/cycle")


async def test_run_claude_passes_model():
    received_options = {}

    async def gen(**kw):
        received_options.update(kw)
        yield _make_result_message()

    with patch("newsparser.claude.runner._sdk_query", side_effect=lambda **kw: gen(**kw)):
        await run_claude("query", model="claude-haiku-4-5-20251001")

    assert received_options.get("options").model == "claude-haiku-4-5-20251001"


async def test_run_claude_loads_mcp_config(tmp_path):
    mcp_file = tmp_path / "mcp.json"
    mcp_file.write_text('{"mcpServers": {"newsparser": {"command": "python"}}}')

    received_options = {}

    async def gen(**kw):
        received_options.update(kw)
        yield _make_result_message()

    with patch("newsparser.claude.runner._sdk_query", side_effect=lambda **kw: gen(**kw)):
        await run_claude("query", mcp_config=str(mcp_file))

    # mcp_servers should be passed as a Path (the file)
    import pathlib
    opts = received_options.get("options")
    assert opts is not None
    assert opts.mcp_servers == mcp_file


async def test_run_claude_no_mcp_config_passes_empty_dict():
    received_options = {}

    async def gen(**kw):
        received_options.update(kw)
        yield _make_result_message()

    with patch("newsparser.claude.runner._sdk_query", side_effect=lambda **kw: gen(**kw)):
        await run_claude("query")

    opts = received_options.get("options")
    assert opts.mcp_servers == {}
