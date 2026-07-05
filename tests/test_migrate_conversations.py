import json

import pytest

from newsparser.store import conversations as conv
from newsparser.scripts import migrate_conversations as mig


@pytest.fixture(autouse=True)
def tmp_conv_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CONV_DB_PATH", str(tmp_path / "conversations.db"))
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    conv.init_conv_db()


def _write_session(ws, chat_id, turns):
    d = ws / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{chat_id}.jsonl").write_text(
        "\n".join(json.dumps(t, ensure_ascii=False) for t in turns), encoding="utf-8"
    )


def _write_events(ws, events):
    d = ws / "me"
    d.mkdir(parents=True, exist_ok=True)
    (d / "interest-events.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8"
    )


def test_migrates_sessions_with_reply_inference_and_order(tmp_path):
    ws = tmp_path / "workspace"
    _write_session(ws, "42", [
        {"role": "user", "content": "q1", "ts": "2026-07-01T00:00:00+00:00"},
        {"role": "assistant", "content": "a1", "ts": "2026-07-01T00:00:00+00:00"},
        {"role": "user", "content": "q2", "ts": "2026-07-02T00:00:00+00:00"},
        {"role": "assistant", "content": "a2", "ts": "2026-07-02T00:00:00+00:00"},
    ])
    turns, rows = mig.migrate_sessions(ws)
    assert (turns, rows) == (4, 4)

    msgs = list(conv.iter_all_messages())
    assert [m["content"] for m in msgs] == ["q1", "a1", "q2", "a2"]  # order preserved
    by_content = {m["content"]: m for m in msgs}
    # assistant turns reply to the preceding user turn; user turns have no parent
    assert by_content["a1"]["reply_to_id"] == by_content["q1"]["id"]
    assert by_content["a2"]["reply_to_id"] == by_content["q2"]["id"]
    assert by_content["q1"]["reply_to_id"] is None
    assert all(m["kind"] == "chat" for m in msgs)
    # source file renamed
    assert not (ws / "sessions" / "42.jsonl").exists()
    assert (ws / "sessions" / "42.jsonl.migrated").exists()


def test_migration_is_idempotent(tmp_path):
    ws = tmp_path / "workspace"
    _write_session(ws, "42", [
        {"role": "user", "content": "q1", "ts": "2026-07-01T00:00:00+00:00"},
        {"role": "assistant", "content": "a1", "ts": "2026-07-01T00:00:00+00:00"},
    ])
    _write_events(ws, [{"ts": "2026-07-01T00:00:00+00:00", "themes": ["엔비디아"]}])

    assert mig.main() == 0
    assert len(list(conv.iter_all_messages())) == 2
    assert conv.interest_theme_counts() == [("엔비디아", 1)]

    # Re-run: files already renamed, message ids already present → no growth.
    assert mig.main() == 0
    assert len(list(conv.iter_all_messages())) == 2
    assert conv.interest_theme_counts() == [("엔비디아", 1)]


def test_migrates_interest_events_multiple_themes(tmp_path):
    ws = tmp_path / "workspace"
    _write_events(ws, [
        {"ts": "2026-07-01T00:00:00+00:00", "themes": ["A", "B"]},
        {"ts": "2026-07-02T00:00:00+00:00", "entities": ["C"]},  # falls back to entities
    ])
    inserted = mig.migrate_interest_events(ws)
    assert inserted == 3
    counts = dict(conv.interest_theme_counts())
    assert counts == {"A": 1, "B": 1, "C": 1}
    assert not (ws / "me" / "interest-events.jsonl").exists()


def test_no_sources_is_noop(tmp_path):
    assert mig.main() == 0
    assert list(conv.iter_all_messages()) == []
