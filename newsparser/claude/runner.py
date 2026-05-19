import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from claude_code_sdk import ClaudeCodeOptions, ClaudeSDKClient, ResultMessage
from claude_code_sdk._errors import MessageParseError
from claude_code_sdk._internal.message_parser import parse_message

logger = logging.getLogger(__name__)

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

    # Using ClaudeSDKClient (not the query() helper): the helper is an async
    # generator wrapping an inner async generator that owns an anyio task
    # group, and `async for` won't propagate aclose() to the inner one, so its
    # task group ends up being torn down by the asyncgen GC finalizer in a
    # foreign task — which trips anyio's "cancel scope exited in a different
    # task" check. ClaudeSDKClient binds connect/disconnect to __aenter__ /
    # __aexit__ coroutines, keeping the task-group lifecycle in this task.
    #
    # We iterate the underlying raw dict stream and parse ourselves so we can
    # skip message types this SDK build does not know about (e.g.
    # rate_limit_event from newer CLI builds): receive_response() would let
    # MessageParseError kill the iterator and abort the whole session.
    try:
        async with asyncio.timeout(timeout):
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                async for data in client._query.receive_messages():
                    try:
                        message = parse_message(data)
                    except MessageParseError as parse_exc:
                        logger.debug("skipping unknown SDK message: %s", parse_exc)
                        continue
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
