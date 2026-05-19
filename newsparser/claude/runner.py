import asyncio
from dataclasses import dataclass
from pathlib import Path

from claude_code_sdk import ClaudeCodeOptions, ResultMessage
from claude_code_sdk import query as _sdk_query

_PROJECT_ROOT = Path(__file__).parent.parent.parent


class ClaudeError(RuntimeError):
    pass


@dataclass
class RunResult:
    text: str
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None


async def run_claude(
    prompt: str,
    *,
    model: str = "claude-sonnet-4-6",
    timeout: int = 1500,
    mcp_config: str | None = None,
    system_prompt: str | None = None,
    allowed_tools: list[str] | None = None,
    permission_mode: str | None = None,
) -> RunResult:
    """Invoke Claude via SDK and return RunResult. Raises ClaudeError on failure."""
    options = ClaudeCodeOptions(
        model=model,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools or [],
        permission_mode=permission_mode,  # type: ignore[arg-type]
        mcp_servers=Path(mcp_config) if mcp_config else {},
        cwd=_PROJECT_ROOT,
    )

    try:
        async with asyncio.timeout(timeout):
            async for message in _sdk_query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    usage = message.usage or {}
                    return RunResult(
                        text=message.result or "",
                        cost_usd=message.total_cost_usd,
                        input_tokens=usage.get("input_tokens"),
                        output_tokens=usage.get("output_tokens"),
                        duration_ms=message.duration_ms,
                    )
    except asyncio.TimeoutError:
        raise ClaudeError(f"claude timed out after {timeout}s")
    except ClaudeError:
        raise
    except Exception as exc:
        raise ClaudeError(str(exc)) from exc

    raise ClaudeError("claude returned no result")
