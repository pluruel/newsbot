import threading
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
import httpx2 as httpx
import pytest

from newsparser.claude import api, runner
from newsparser.claude.runner import ClaudeError, ClaudeKilled


def _msg(stop_reason: str, text: str):
    content = [SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(stop_reason=stop_reason, content=content)


class _FakeStream:
    def __init__(self, owner):
        self.owner = owner
        self._closed = threading.Event()

    def __enter__(self):
        if self.owner.raise_on_enter is not None:
            raise self.owner.raise_on_enter
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._closed.set()

    def __iter__(self):
        yield SimpleNamespace(type="content_block_delta")
        if self.owner.block_until_closed:
            self._closed.wait(5)
            # a closed httpx stream surfaces as a read error mid-iteration
            raise RuntimeError("stream closed")
        yield SimpleNamespace(type="message_stop")

    def get_final_message(self):
        return self.owner.final


class _FakeClient:
    def __init__(self):
        self.final = None
        self.block_until_closed = False
        self.raise_on_enter = None
        self.last_kwargs = None
        self.messages = self

    def stream(self, **kw):
        self.last_kwargs = kw
        return _FakeStream(self)


@pytest.fixture
def fake_client():
    client = _FakeClient()
    with patch("newsparser.claude.api.get_client", return_value=client):
        yield client


def test_ask_claude_returns_text_and_passes_params(fake_client):
    fake_client.final = _msg("end_turn", "리포트 본문")
    assert api.ask_claude("u", "sys", timeout=10) == "리포트 본문"
    kw = fake_client.last_kwargs
    assert kw["model"] == "claude-sonnet-5" and kw["max_tokens"] == 64000
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["effort"] == "high"
    assert kw["system"] == "sys"
    assert kw["messages"] == [{"role": "user", "content": "u"}]


def test_ask_claude_sets_json_schema_format(fake_client):
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    fake_client.final = _msg("end_turn", "{}")
    api.ask_claude("u", "s", timeout=10, output_schema=schema)
    assert fake_client.last_kwargs["output_config"]["format"] == {"type": "json_schema", "schema": schema}


def test_ask_claude_raises_on_max_tokens(fake_client):
    fake_client.final = _msg("max_tokens", "잘린")
    with pytest.raises(ClaudeError, match="max_tokens"):
        api.ask_claude("u", "s", timeout=10)


def test_ask_claude_raises_on_refusal(fake_client):
    fake_client.final = _msg("refusal", "")
    with pytest.raises(ClaudeError, match="refusal"):
        api.ask_claude("u", "s", timeout=10)


def test_ask_claude_kill_raises_claude_killed(fake_client):
    fake_client.block_until_closed = True     # 이터레이션이 close()까지 대기
    token = runner.CURRENT_JOB.set(7)
    try:
        t = threading.Timer(0.2, lambda: runner.kill_job(7)); t.start()
        with pytest.raises(ClaudeKilled):
            api.ask_claude("u", "s", timeout=10)
    finally:
        runner.CURRENT_JOB.reset(token)
    assert not [r for r in runner.active_runs() if r["job_id"] == 7]


def test_ask_claude_wall_clock_timeout(fake_client):
    fake_client.block_until_closed = True
    with pytest.raises(ClaudeError, match="timed out after 0.3s"):
        api.ask_claude("u", "s", timeout=0.3)


def test_ask_claude_wraps_api_errors(fake_client):
    fake_client.raise_on_enter = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
    with pytest.raises(ClaudeError):
        api.ask_claude("u", "s", timeout=10)


def test_ask_claude_unregisters_run_on_failure(fake_client):
    fake_client.final = _msg("max_tokens", "x")
    token = runner.CURRENT_JOB.set(9)
    try:
        with pytest.raises(ClaudeError):
            api.ask_claude("u", "s", timeout=10)
    finally:
        runner.CURRENT_JOB.reset(token)
    assert not [r for r in runner.active_runs() if r["job_id"] == 9]


def test_get_client_requires_newsbot_api_key(monkeypatch):
    monkeypatch.delenv("NEWSBOT_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")   # 더 이상 대체 경로 아님
    api.reset_client()
    with pytest.raises(ClaudeError, match="NEWSBOT_API_KEY"):
        api.get_client()


def test_get_client_is_cached_until_reset(monkeypatch):
    monkeypatch.setenv("NEWSBOT_API_KEY", "k")
    api.reset_client()
    try:
        assert api.get_client() is api.get_client()
    finally:
        api.reset_client()


def test_check_credentials_logs_error_when_missing(monkeypatch, caplog):
    monkeypatch.delenv("NEWSBOT_API_KEY", raising=False)
    assert api.check_credentials() is False
    assert "NEWSBOT_API_KEY" in caplog.text


def test_check_credentials_true_when_set(monkeypatch):
    monkeypatch.setenv("NEWSBOT_API_KEY", "k")
    assert api.check_credentials() is True
