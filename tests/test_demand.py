import pytest

from newsparser.store import conversations as conv
from newsparser.scheduler.demand import build_demand_digest, write_demand_digest


@pytest.fixture(autouse=True)
def tmp_conv_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CONV_DB_PATH", str(tmp_path / "conversations.db"))
    conv.init_conv_db()


def test_digest_empty_when_no_signal():
    out = build_demand_digest("2026-07-05", days=14)
    assert "수집된 대화 수요 신호 없음" in out


def test_digest_includes_themes_and_queries():
    conv.log_interest_event("엔비디아", ts="2026-07-01T00:00:00+00:00")
    conv.log_interest_event("엔비디아", ts="2026-07-02T00:00:00+00:00")
    conv.add_message("c1", "user", "엔비디아 실적 어땠어", ts="2026-07-01T09:00:00+00:00")
    out = build_demand_digest("2026-07-05", days=14)
    assert "엔비디아 (2회)" in out
    assert "엔비디아 실적 어땠어" in out


def test_digest_respects_window():
    # An event 20 days before the reference date is outside a 14-day window.
    conv.log_interest_event("옛날테마", ts="2026-06-10T00:00:00+00:00")
    conv.add_message("c1", "user", "오래된질문", ts="2026-06-10T00:00:00+00:00")
    out = build_demand_digest("2026-07-05", days=14)
    assert "옛날테마" not in out
    assert "오래된질문" not in out


def test_only_user_chat_turns_in_queries():
    conv.add_message("c1", "user", "내질문", ts="2026-07-01T00:00:00+00:00")
    conv.add_message("c1", "assistant", "봇답변", ts="2026-07-01T00:00:01+00:00")
    conv.add_message("c1", "user", "관리자명령", kind="admin", ts="2026-07-01T00:00:02+00:00")
    out = build_demand_digest("2026-07-05", days=14)
    assert "내질문" in out
    assert "봇답변" not in out       # assistant turns excluded
    assert "관리자명령" not in out    # admin turns excluded


def test_write_demand_digest_creates_file(tmp_path):
    conv.add_message("c1", "user", "질문", ts="2026-07-01T00:00:00+00:00")
    path = write_demand_digest(tmp_path / "workspace", "2026-07-05", days=7)
    assert path.exists()
    assert path.name == "interest-demand.md"
    assert "질문" in path.read_text()
