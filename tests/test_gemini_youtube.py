"""Gemini YouTube path — MCP-client wiring and how failures surface.

The Vertex call lives on a separate MCP server (mcp.md); here the transport is
stubbed at `_call_gemini_interact`, so what is asserted is the tool request this
project builds and the error handling around it. No test reaches the network.
"""
from types import SimpleNamespace

import pytest

import newsparser.gemini as gemini
from newsparser.gemini import GeminiError

_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture(autouse=True)
def mcp_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MCP_URL", "https://vertex.example.internal/mcp")
    monkeypatch.setenv("GEMINI_MCP_TOKEN", "test-token")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


class _FakeTransport:
    """Stands in for `_call_gemini_interact`; records (url, token, arguments)."""

    def __init__(self, text="요약입니다", error=None):
        self.calls = []
        self._text = text
        self._error = error

    async def __call__(self, url, token, arguments, timeout):
        self.calls.append(SimpleNamespace(
            url=url, token=token, arguments=arguments, timeout=timeout
        ))
        if self._error is not None:
            raise self._error
        return self._text


def _install(monkeypatch, transport):
    monkeypatch.setattr(gemini, "_call_gemini_interact", transport)
    return transport


@pytest.mark.parametrize("missing", ["GEMINI_MCP_URL", "GEMINI_MCP_TOKEN"])
def test_unconfigured_server_raises_rather_than_calling_out(monkeypatch, missing):
    monkeypatch.delenv(missing)
    transport = _install(monkeypatch, _FakeTransport())
    with pytest.raises(GeminiError, match="Gemini MCP 서버가 설정되지 않았습니다"):
        gemini.summarize_youtube(_URL)
    assert transport.calls == []


def test_request_targets_the_configured_server_with_bearer_token(monkeypatch):
    transport = _install(monkeypatch, _FakeTransport())
    gemini.summarize_youtube(_URL, timeout=120)
    call = transport.calls[0]
    assert call.url == "https://vertex.example.internal/mcp"
    assert call.token == "test-token"
    assert call.timeout == 120
    assert call.arguments["timeout_s"] == 120


def test_youtube_url_is_sent_as_a_video_part(monkeypatch):
    transport = _install(monkeypatch, _FakeTransport())
    gemini.summarize_youtube(_URL)
    args = transport.calls[0].arguments
    assert args["model"] == gemini.DEFAULT_MODEL
    assert {"type": "video", "uri": _URL} in args["input"]
    assert args["input"][0]["type"] == "text"


def test_system_prompt_carries_the_shared_plain_text_style(monkeypatch):
    """The reply goes straight to Telegram, which renders no markdown."""
    transport = _install(monkeypatch, _FakeTransport())
    gemini.summarize_youtube(_URL)
    assert gemini.PLAIN_KOREAN_STYLE in transport.calls[0].arguments["system_instruction"]


def test_user_instruction_is_forwarded(monkeypatch):
    transport = _install(monkeypatch, _FakeTransport())
    gemini.summarize_youtube(_URL, "3분대 발언 위주로")
    texts = [p.get("text", "") for p in transport.calls[0].arguments["input"]]
    assert any("3분대 발언 위주로" in t for t in texts)


def test_model_is_overridable_by_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    transport = _install(monkeypatch, _FakeTransport())
    gemini.summarize_youtube(_URL)
    assert transport.calls[0].arguments["model"] == "gemini-2.5-flash"


def test_tool_error_from_server_is_reported_as_is(monkeypatch):
    """Blocked / unreachable video: the server raises, the reason reaches the user."""
    _install(monkeypatch, _FakeTransport(
        error=GeminiError("유튜브 분석에 실패했습니다: status=BLOCKED")
    ))
    with pytest.raises(GeminiError, match="BLOCKED"):
        gemini.summarize_youtube(_URL)


def test_blank_text_raises_instead_of_returning_a_blank_reply(monkeypatch):
    _install(monkeypatch, _FakeTransport(text="   "))
    with pytest.raises(GeminiError, match="빈 응답"):
        gemini.summarize_youtube(_URL)


def test_transport_exception_is_wrapped(monkeypatch):
    _install(monkeypatch, _FakeTransport(error=ConnectionError("refused")))
    with pytest.raises(GeminiError, match="Gemini MCP 호출 실패: ConnectionError"):
        gemini.summarize_youtube(_URL)


def test_tool_result_error_flag_becomes_gemini_error(monkeypatch):
    """`_call_gemini_interact` itself: isError results must not pass through as text."""
    import asyncio
    from contextlib import asynccontextmanager

    class _Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def initialize(self): pass
        async def call_tool(self, name, arguments, read_timeout_seconds=None):
            assert name == "gemini_interact"
            return SimpleNamespace(
                isError=True,
                content=[SimpleNamespace(type="text", text="ValueError: video host not allowed")],
            )

    @asynccontextmanager
    async def _client(url, headers, timeout, sse_read_timeout):
        assert headers == {"Authorization": "Bearer tok"}
        yield (None, None, None)

    monkeypatch.setattr("mcp.client.streamable_http.streamablehttp_client", _client)
    monkeypatch.setattr("mcp.client.session.ClientSession", lambda r, w: _Session())
    with pytest.raises(GeminiError, match="video host not allowed"):
        asyncio.run(gemini._call_gemini_interact("http://x", "tok", {}, 10))


# --- link detection / routing decision ---------------------------------------

_ID = "dQw4w9WgXcQ"
_CANON = f"https://www.youtube.com/watch?v={_ID}"


@pytest.mark.parametrize("text", [
    f"https://www.youtube.com/watch?v={_ID}",
    f"https://youtube.com/watch?v={_ID}",
    f"http://m.youtube.com/watch?v={_ID}",
    f"https://youtu.be/{_ID}",
    f"https://www.youtube.com/shorts/{_ID}",
    f"https://www.youtube.com/live/{_ID}",
    f"https://www.youtube.com/embed/{_ID}",
    f"https://youtu.be/{_ID}?t=30",
    f"https://m.youtube.com/watch?app=desktop&v={_ID}",
    f"https://www.youtube.com/watch?v={_ID}&list=PL123&index=2",
    f"(https://www.youtube.com/watch?v={_ID})",
    f"봐봐 https://youtu.be/{_ID}.",
])
def test_every_link_shape_normalises_to_one_watch_url(text):
    """Only the watch URL is sent, so youtu.be/shorts/live need no API support."""
    assert gemini.find_youtube_url(text)[0] == _CANON


def test_surrounding_text_becomes_the_instruction():
    assert gemini.find_youtube_url(f"이거 요약해줘 https://youtu.be/{_ID} 3분대 위주로") == (
        _CANON, "이거 요약해줘 3분대 위주로"
    )


def test_bare_link_has_no_instruction():
    assert gemini.find_youtube_url(f"(https://www.youtube.com/watch?v={_ID})") == (_CANON, "")


@pytest.mark.parametrize("text", [
    "FOMC 어떻게 됐어?",
    "",
    f"https://example.com/watch?v={_ID}",
    "https://www.youtube.com/watch?v=short",   # id too short
    "https://vimeo.com/123456789",
])
def test_non_youtube_input_routes_to_the_tracker(text):
    assert gemini.find_youtube_url(text) is None


def test_first_link_wins_when_two_are_sent():
    assert gemini.find_youtube_url(f"https://youtu.be/{_ID} https://youtu.be/aBcDeFgHiJk")[0] == _CANON
