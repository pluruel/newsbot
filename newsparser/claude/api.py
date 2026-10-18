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

    `timeout` is a wall-clock ceiling covering the whole call, including the
    wait for response headers: the run is registered and the timer started
    before the request is sent. The timer cannot abort a request still in
    flight (there is no stream to close yet), so the ceiling is enforced the
    moment the stream opens or, on a stalled connection, once the 120s read
    timeout below fires — at most ~120s late. SDK retries stay at their
    defaults.

    Streaming because a 64k-token adaptive-thinking answer outlives any
    non-streaming HTTP request. The call is registered with the runner
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

    timed_out = threading.Event()

    def _raise_if_interrupted() -> None:
        # Closing the stream may end the iteration quietly or raise something
        # incidental, so a kill / timeout is decided here, before any generic
        # failure handling. Kill wins: it is the user's explicit act.
        if run.killed:
            raise ClaudeKilled(f"claude run killed by kill request (job #{run.job_id})")
        if timed_out.is_set():
            raise ClaudeError(f"claude timed out after {timeout}s")

    stream = None

    def _cancel() -> None:
        # Before the stream is open there is nothing to close: kill_job has
        # already set run.killed, and the post-open check below acts on it.
        if stream is not None:
            stream.close()

    def _expire() -> None:
        timed_out.set()
        try:
            _cancel()
        except Exception:
            pass

    # Registered and timed before the request is sent, so kill and the
    # wall-clock ceiling cover the wait for response headers too.
    run = register_run(prompt, cancel=_cancel)
    timer = threading.Timer(timeout, _expire)
    timer.daemon = True
    timer.start()
    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
            output_config=output_config,
            # read=120: on Linux a cross-thread close() does not wake a thread
            # blocked in recv, so this is what makes a stalled-then-closed
            # socket surface.
            timeout=httpx2.Timeout(timeout, connect=5.0, read=120.0),
        ) as opened:
            stream = opened
            # Kill/timeout may have fired while the request was in flight.
            _raise_if_interrupted()
            for _ in opened:
                note_activity(run, "응답 작성")
            _raise_if_interrupted()
            message = opened.get_final_message()
        _raise_if_interrupted()
    except ClaudeError:
        raise
    except Exception as exc:
        _raise_if_interrupted()
        if isinstance(exc, anthropic.APIStatusError):
            raise ClaudeError(f"claude api error {exc.status_code} ({exc.type})") from exc
        # AssertionError: the SDK asserts in get_final_message() when the
        # stream ended without a single event.
        if isinstance(exc, (anthropic.APIError, httpx2.HTTPError, httpx2.StreamError,
                            AssertionError)):
            raise ClaudeError(f"claude api failure: {exc!r}") from exc
        raise
    finally:
        timer.cancel()
        unregister_run(run)

    if message.stop_reason != "end_turn":
        raise ClaudeError(f"claude stopped with stop_reason={message.stop_reason}")
    return "".join(block.text for block in message.content if block.type == "text")
