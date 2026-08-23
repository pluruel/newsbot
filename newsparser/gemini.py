"""Vertex AI (Gemini) path — used only when a chat message carries a YouTube link.

Claude cannot watch a video; Gemini takes a YouTube URL directly as a content
part, so that one case routes here instead of through the tracker. Everything
else in the system stays on the Claude paths.

The actual Vertex call is made by an internal MCP server (``mcp.md``) that
holds the GCP service-account key — this host never has Google credentials.
The tracker runs with Bash on scraped article text in context, so a key on
this disk is one injected line away from leaking. Over here only the prompt
is built and the reply relayed; the server exposes one tool,
``gemini_interact(model, input, system_instruction, timeout_s) -> str``.

Config: ``GEMINI_MCP_URL`` and ``GEMINI_MCP_TOKEN`` (bearer). Missing either
means no YouTube analysis — the caller reports the error rather than silently
answering without having seen the video.
"""
import asyncio
import logging
import os
import re
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.7-flash"
# TelegramSender truncates at 4096 chars, so an over-long summary loses its tail
# with no error. Ask for less than that rather than relying on the model.
_MAX_CHARS = 3000

# Both answer paths (this one and the tracker's Claude prompt) send their text
# straight to Telegram, which renders no markdown. One constant so the two
# paths cannot drift into different voices.
PLAIN_KOREAN_STYLE = (
    "답변은 사용자에게 존댓말로 쓴다. 반말·평어체는 쓰지 않는다.\n"
    "형식은 평문 대화체 문단으로만 한다. 마크다운 금지: 헤더(#), "
    "볼드(**), 불릿(-/*), 표, 수평선(---) 모두 쓰지 않는다. "
    "섹션 구분은 빈 줄로만 한다."
)

# Normalising every link shape to one watch URL means youtu.be / shorts / live
# links work without betting on the API accepting each variant. Ids are exactly
# 11 chars of [A-Za-z0-9_-]; anchoring on that length keeps trailing punctuation
# ("…watch?v=abc123DEF45.") out of the id and lets "watch?app=desktop&v=ID" match.
_YOUTUBE_RE = re.compile(
    r"https?://(?:www\.|m\.)?"
    r"(?:youtube\.com/(?:watch\?(?:\S*?&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
    r"(?:[?&]\S*)?"
)


def find_youtube_url(text: str) -> tuple[str, str] | None:
    """(watch URL, the rest of the message) for the first YouTube link, else None.

    This is the whole routing decision for the chat path. The remainder is what
    the user typed around the link ("3분대 발언 위주로"), which steers the summary.
    """
    match = _YOUTUBE_RE.search(text)
    if match is None:
        return None
    url = f"https://www.youtube.com/watch?v={match.group(1)}"
    rest = " ".join((text[: match.start()] + " " + text[match.end():]).split())
    return url, rest.strip(" ()[]<>")


class GeminiError(RuntimeError):
    pass


_SYSTEM_PROMPT = (
    "너는 시장·기술 뉴스를 다루는 개인 인텔리전스 어시스턴트다. "
    "사용자가 보낸 유튜브 영상을 직접 보고 내용을 정리해 전달한다.\n\n"
    "근거 규칙:\n"
    "- 영상에서 실제로 보고 들은 내용만 쓴다. 일반 지식이나 배경 추측으로 빈틈을 메우지 않는다.\n"
    "- 수치, 티커, 날짜, 고유명사, 발언은 영상에 나온 그대로 정확히 옮긴다. "
    "임의로 반올림하지 않는다.\n"
    "- 영어 발화는 자연스러운 한국어로 옮기되 티커와 영어 고유명사는 원문 그대로 둔다.\n"
    "- 화자의 주장과 네 해석을 섞지 않는다. 해석을 덧붙일 때는 그것이 해석임을 밝힌다.\n\n"
    f"길이는 {_MAX_CHARS}자 이내로 맞춘다.\n\n"
    f"{PLAIN_KOREAN_STYLE}"
)


def mcp_config() -> tuple[str, str]:
    """(url, bearer token) of the Gemini MCP server, or GeminiError if unset."""
    url = os.environ.get("GEMINI_MCP_URL", "").strip()
    token = os.environ.get("GEMINI_MCP_TOKEN", "").strip()
    if not url or not token:
        raise GeminiError(
            "Gemini MCP 서버가 설정되지 않았습니다 (GEMINI_MCP_URL / GEMINI_MCP_TOKEN)"
        )
    return url, token


async def _call_gemini_interact(
    url: str, token: str, arguments: dict[str, Any], timeout: float
) -> str:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {token}"}
    # The video analysis itself is the slow part; the per-request HTTP timeout
    # and the SSE read timeout both have to cover it, not just the handshake.
    async with streamablehttp_client(
        url, headers=headers, timeout=timeout, sse_read_timeout=timeout
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "gemini_interact",
                arguments,
                read_timeout_seconds=timedelta(seconds=timeout),
            )
    text = "\n".join(
        c.text for c in result.content if getattr(c, "type", None) == "text"
    )
    if result.isError:
        # The server raises a tool error for API failures and for blank
        # output (blocked / unreachable video); the message carries the reason.
        raise GeminiError(f"유튜브 분석에 실패했습니다: {text or 'unknown error'}")
    return text


def summarize_youtube(url: str, instruction: str = "", timeout: float = 300.0) -> str:
    """Watch `url` and return a plain-text Korean summary.

    `instruction` is whatever the user typed around the link — it steers the
    summary ("3분대 발언 위주로"). Empty means a general summary.

    Sends the prompt and the watch URL to the Gemini MCP server's
    `gemini_interact` tool, which forwards them to Vertex's interactions API
    (VideoContent takes a YouTube URL directly). Public videos only.

    Runs on a PTB worker thread (`ctx.run_in_thread`), so the async MCP client
    is driven with `asyncio.run()` here.

    Raises GeminiError when the server is unconfigured, unreachable, or returns
    a tool error.
    """
    mcp_url, token = mcp_config()
    ask = (
        "이 영상의 내용을 정리해줘. 무엇에 대한 영상인지, 화자가 내세우는 핵심 주장과 "
        "결론이 무엇인지, 그 근거로 제시한 수치·사례가 무엇인지 짚어줘."
    )
    if instruction:
        ask += f"\n\n사용자 요청: {instruction}"

    arguments = {
        "model": os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
        "input": [{"type": "text", "text": ask}, {"type": "video", "uri": url}],
        "system_instruction": _SYSTEM_PROMPT,
        "timeout_s": int(timeout),
    }
    try:
        text = asyncio.run(_call_gemini_interact(mcp_url, token, arguments, timeout))
    except GeminiError:
        raise
    except Exception as exc:
        # The MCP client runs inside an anyio TaskGroup, so a transport error
        # surfaces as "ExceptionGroup: unhandled errors in a TaskGroup" — useless
        # in a Telegram reply. Report the leaf exception (e.g. ConnectError)
        # instead.
        leaf = exc
        while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:
            leaf = leaf.exceptions[0]
        raise GeminiError(f"Gemini MCP 호출 실패: {type(leaf).__name__}: {leaf}") from exc

    text = text.strip()
    if not text:
        raise GeminiError(f"Gemini MCP 서버가 빈 응답을 반환했습니다. url={url}")
    return text
