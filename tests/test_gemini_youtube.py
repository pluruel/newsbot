"""Gemini/Vertex YouTube path — credential handling and the chat-path wiring.

No test reaches the network: the SDK client is stubbed, so what is asserted is
the request this project builds and how failures surface to the user.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import newsparser.gemini as gemini
from newsparser.gemini import GeminiError


@pytest.fixture(autouse=True)
def fresh_client(monkeypatch, tmp_path):
    gemini.reset_client()
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setenv("GCP_KEY_FILE", str(tmp_path / "gcp-key.json"))
    yield
    gemini.reset_client()


def _write_key(monkeypatch, tmp_path, project_id="proj-123"):
    path = tmp_path / "gcp-key.json"
    path.write_text(json.dumps({"type": "service_account", "project_id": project_id}))
    monkeypatch.setenv("GCP_KEY_FILE", str(path))
    return path


class _FakeInteractions:
    def __init__(self, output_text="요약입니다", errors=None, status="COMPLETED"):
        self.calls = []
        self._result = SimpleNamespace(
            output_text=output_text, errors=errors, status=status
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


def _install_fake_client(monkeypatch, interactions):
    monkeypatch.setattr(
        gemini, "_build_client", lambda: SimpleNamespace(interactions=interactions)
    )


def test_credentials_available_tracks_the_key_file(monkeypatch, tmp_path):
    assert gemini.credentials_available() is False
    _write_key(monkeypatch, tmp_path)
    assert gemini.credentials_available() is True


def test_missing_key_file_raises_rather_than_calling_out(monkeypatch, tmp_path):
    monkeypatch.setenv("GCP_KEY_FILE", str(tmp_path / "absent.json"))
    with pytest.raises(GeminiError, match="GCP 키 파일이 없습니다"):
        gemini.summarize_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_key_without_project_id_raises(monkeypatch, tmp_path):
    path = tmp_path / "gcp-key.json"
    path.write_text(json.dumps({"type": "service_account"}))
    monkeypatch.setenv("GCP_KEY_FILE", str(path))
    with pytest.raises(GeminiError, match="project_id"):
        gemini.summarize_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_client_is_built_from_the_key_file(monkeypatch, tmp_path):
    _write_key(monkeypatch, tmp_path, project_id="newsbot-proj")
    captured = {}

    class _FakeCreds:
        @staticmethod
        def from_service_account_file(path, scopes):
            captured["scopes"] = scopes
            return "creds"

    def fake_client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(interactions=_FakeInteractions())

    with patch.dict("sys.modules", {}):
        monkeypatch.setattr(
            "google.oauth2.service_account.Credentials", _FakeCreds, raising=False
        )
        monkeypatch.setattr("google.genai.Client", fake_client)
        gemini.summarize_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert captured["vertexai"] is True
    assert captured["project"] == "newsbot-proj"
    assert captured["location"] == "global"
    assert captured["credentials"] == "creds"


def test_youtube_url_is_sent_as_a_video_part(monkeypatch, tmp_path):
    _write_key(monkeypatch, tmp_path)
    fake = _FakeInteractions()
    _install_fake_client(monkeypatch, fake)

    gemini.summarize_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    body = fake.calls[0]
    assert body["model"] == gemini.DEFAULT_MODEL
    serialized = [part.model_dump() for part in body["input"]]
    assert {
        "type": "video",
        "uri": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    } in serialized


def test_system_prompt_carries_the_shared_plain_text_style(monkeypatch, tmp_path):
    """The reply goes straight to Telegram, which renders no markdown."""
    from newsparser.gemini import PLAIN_KOREAN_STYLE

    _write_key(monkeypatch, tmp_path)
    fake = _FakeInteractions()
    _install_fake_client(monkeypatch, fake)

    gemini.summarize_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert PLAIN_KOREAN_STYLE in fake.calls[0]["system_instruction"]


def test_user_instruction_is_forwarded(monkeypatch, tmp_path):
    _write_key(monkeypatch, tmp_path)
    fake = _FakeInteractions()
    _install_fake_client(monkeypatch, fake)

    gemini.summarize_youtube(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "3분대 발언 위주로"
    )

    texts = [p.model_dump().get("text", "") for p in fake.calls[0]["input"]]
    assert any("3분대 발언 위주로" in t for t in texts)


def test_model_is_overridable_by_env(monkeypatch, tmp_path):
    _write_key(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    fake = _FakeInteractions()
    _install_fake_client(monkeypatch, fake)

    gemini.summarize_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert fake.calls[0]["model"] == "gemini-2.5-flash"


def test_empty_output_raises_instead_of_returning_a_blank_reply(monkeypatch, tmp_path):
    """A blocked or unreachable video must not be delivered as an empty message."""
    _write_key(monkeypatch, tmp_path)
    _install_fake_client(monkeypatch, _FakeInteractions(output_text="", errors="BLOCKED"))

    with pytest.raises(GeminiError, match="빈 응답"):
        gemini.summarize_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_api_exception_is_wrapped(monkeypatch, tmp_path):
    _write_key(monkeypatch, tmp_path)

    class _Boom:
        def create(self, **kwargs):
            raise RuntimeError("503 unavailable")

    _install_fake_client(monkeypatch, _Boom())
    with pytest.raises(GeminiError, match="Gemini 호출 실패"):
        gemini.summarize_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


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
