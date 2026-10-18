"""Direct Anthropic API path: the shared SDK client plus the streaming
single-call `ask_claude` the scheduled jobs (cycle/weekly/reflect) run on.
Haiku's `ask_haiku` (haiku.py) shares the client. See CLAUDE_DEV.md
"Architecture" for why these bypass the CLI and how auth works."""
import logging
import os
import threading

import anthropic
import httpx2

from newsparser.claude.runner import (
    ClaudeError, ClaudeKilled, note_activity, register_run, unregister_run,
)

logger = logging.getLogger(__name__)

SCHEDULED_MODEL = "claude-sonnet-5"
# Not ANTHROPIC_API_KEY: the tracker's `claude -p` subprocess inherits this
# process's environment, and the CLI prefers ANTHROPIC_API_KEY over its OAuth
# token — that name would move the chat answers onto API billing too.
API_KEY_ENV = "NEWSBOT_API_KEY"

_client: anthropic.Anthropic | None = None
_client_lock = threading.Lock()


def get_client() -> anthropic.Anthropic:
    """Built once and shared across PTB worker threads, the poller loop and the
    job threads."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                api_key = os.environ.get(API_KEY_ENV)
                if not api_key:
                    raise ClaudeError(f"no API credential: set {API_KEY_ENV}")
                _client = anthropic.Anthropic(api_key=api_key)
    return _client


def reset_client() -> None:
    """Drop the cached client so the next call re-reads the environment."""
    global _client
    with _client_lock:
        _client = None


def check_credentials() -> bool:
    """Startup check for the long-running processes: a missing key would
    otherwise only surface at the first triage/job call, hours later."""
    if os.environ.get(API_KEY_ENV):
        return True
    logger.error("%s is not set — triage, classifiers and scheduled jobs will fail", API_KEY_ENV)
    return False


def ask_claude(
    prompt: str,
    system: str,
    *,
    timeout: float,
    max_tokens: int = 64000,
    output_schema: dict | None = None,
    model: str = SCHEDULED_MODEL,
) -> str:
    """Run one tool-less, streaming turn and return its text blocks joined.

    `timeout` is a wall-clock ceiling enforced by a timer that closes the
    stream; SDK retries stay at their defaults (they only cover opening the
    request), so the timer is what bounds the call, not the SDK.

    Streaming because a 64k-token adaptive-thinking answer outlives any
    non-streaming HTTP request. The open stream is registered with the runner
    so kill_job() can cancel it by closing the stream.

    `output_schema` constrains the reply to that JSON schema (structured
    outputs); the returned text is then the JSON document.

    Raises ClaudeKilled on a kill request, ClaudeError on timeout, any
    API/transport failure, or a reply that did not end normally (max_tokens
    truncation and refusals included).
    """
    client = get_client()
    output_config: dict = {"effort": "high"}
    if output_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": output_schema}

    run = None
    timed_out = threading.Event()
    timer: threading.Timer | None = None

    def _raise_if_interrupted() -> None:
        # Closing the stream may end the iteration quietly or raise something
        # incidental, so a kill / timeout is decided here, before any generic
        # failure handling. Kill wins: it is the user's explicit act.
        if run is not None and run.killed:
            raise ClaudeKilled(f"claude run killed by kill request (job #{run.job_id})")
        if timed_out.is_set():
            raise ClaudeError(f"claude timed out after {timeout}s")

    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
            output_config=output_config,
        ) as stream:
            run = register_run(prompt, cancel=stream.close)

            def _expire() -> None:
                timed_out.set()
                try:
                    stream.close()
                except Exception:
                    pass

            timer = threading.Timer(timeout, _expire)
            timer.daemon = True
            timer.start()
            for _ in stream:
                note_activity(run, "응답 작성")
            _raise_if_interrupted()
            message = stream.get_final_message()
        _raise_if_interrupted()
    except ClaudeError:
        raise
    except Exception as exc:
        _raise_if_interrupted()
        if isinstance(exc, anthropic.APIStatusError):
            raise ClaudeError(f"claude api error {exc.status_code} ({exc.type})") from exc
        if isinstance(exc, (anthropic.APIError, httpx2.HTTPError)):
            raise ClaudeError(f"claude api failure: {exc}") from exc
        raise
    finally:
        if timer is not None:
            timer.cancel()
        if run is not None:
            unregister_run(run)

    if message.stop_reason != "end_turn":
        raise ClaudeError(f"claude stopped with stop_reason={message.stop_reason}")
    return "".join(block.text for block in message.content if block.type == "text")
