"""Direct-API path for the short, tool-less Haiku calls. See CLAUDE_DEV.md
"Architecture" for why these bypass the CLI. The client and its single
NEWSBOT_API_KEY credential live in api.py."""
import logging

import anthropic

from newsparser.claude.api import get_client, reset_client  # noqa: F401  (reset_client re-exported for callers)
from newsparser.claude.runner import ClaudeError

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-5-5"


def ask_haiku(
    prompt: str,
    system_prompt: str,
    max_tokens: int,
    timeout: float = 30.0,
    model: str = HAIKU_MODEL,
    usage_tag: str | None = None,
    output_schema: dict | None = None,
) -> str:
    """Run one tool-less Haiku turn and return its text.

    `timeout` is a wall-clock ceiling: SDK retries are disabled so one call is
    one HTTP attempt, matching what `run_claude` gave every call site. Do not
    re-enable them without shrinking the call sites' declared timeouts — two of
    them already run their own retry loop.

    Size `max_tokens` to the longest legitimate answer: a truncated reply comes
    back as ordinary text with no exception, so a caller's parser would see a
    short answer rather than an error.

    `usage_tag` opts a call site into token accounting: the call's input/output
    token counts are accumulated per (day, tag) in the haiku_usage table.
    Recording is fail-open — an accounting error never fails the call.

    `output_schema` constrains the reply to that JSON schema (structured
    outputs, `output_config.format`); the returned text is then the JSON
    document. A reply cut at `max_tokens` can still be incomplete JSON, so
    callers parse defensively.

    Thinking is off: Haiku 5.5 thinks by default and thinking counts toward
    `max_tokens`, so the 8-32-token classifier caps would be spent before any
    text. Disabled thinking is only accepted at effort `high` or below.

    Raises ClaudeError on any API or transport failure.
    """
    client = get_client()
    output_config: dict = {"effort": "low"}
    if output_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": output_schema}
    try:
        message = client.with_options(timeout=timeout, max_retries=0).messages.create(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "disabled"},
            output_config=output_config,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIStatusError as exc:
        raise ClaudeError(f"haiku api error {exc.status_code} ({exc.type})") from exc
    except anthropic.APIError as exc:
        raise ClaudeError(f"haiku api failure: {exc}") from exc

    if usage_tag is not None:
        try:
            # Local import: the store layer imports nothing from claude/, but
            # keeping this out of module scope avoids widening haiku.py's
            # import-time dependencies for the call sites that don't account.
            from newsparser.store.sqlite import record_haiku_usage
            record_haiku_usage(usage_tag,
                               message.usage.input_tokens,
                               message.usage.output_tokens)
        except Exception as exc:
            logger.warning("haiku usage recording failed (tag=%s): %s", usage_tag, exc)

    if message.stop_reason == "max_tokens":
        logger.warning(
            "haiku reply hit max_tokens=%d — output may be truncated (prompt head: %r)",
            max_tokens, prompt[:80],
        )
    return "".join(block.text for block in message.content if block.type == "text")
