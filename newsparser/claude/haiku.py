"""Direct-API path for the short, tool-less Haiku calls. See CLAUDE_DEV.md
"Architecture" for why these bypass the CLI and how auth works."""
import logging
import os
import threading

import anthropic

from newsparser.claude.runner import ClaudeError

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5"
_OAUTH_BETA = "oauth-2025-04-20"

_client: anthropic.Anthropic | None = None
_client_lock = threading.Lock()


def _build_client() -> anthropic.Anthropic:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic(default_headers={"anthropic-beta": _OAUTH_BETA})
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        raise ClaudeError(
            "no API credential: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN"
        )
    return anthropic.Anthropic(
        auth_token=token, default_headers={"anthropic-beta": _OAUTH_BETA}
    )


def _get_client() -> anthropic.Anthropic:
    """Built once and shared across PTB worker threads and the poller loop."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = _build_client()
    return _client


def reset_client() -> None:
    """Drop the cached client so the next call re-reads the environment."""
    global _client
    with _client_lock:
        _client = None


def ask_haiku(
    prompt: str,
    system_prompt: str,
    max_tokens: int,
    timeout: float = 30.0,
    model: str = HAIKU_MODEL,
    usage_tag: str | None = None,
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

    Raises ClaudeError on any API or transport failure.
    """
    client = _get_client()
    try:
        message = client.with_options(timeout=timeout, max_retries=0).messages.create(
            model=model,
            max_tokens=max_tokens,
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
