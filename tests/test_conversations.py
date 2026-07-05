import pytest

from newsparser.store import conversations as conv


@pytest.fixture(autouse=True)
def tmp_conv_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CONV_DB_PATH", str(tmp_path / "conversations.db"))
    conv.init_conv_db()


def test_init_is_idempotent():
    conv.init_conv_db()
    conv.init_conv_db()  # must not raise


def test_add_and_get_recent():
    conv.add_message("c1", "user", "안녕")
    conv.add_message("c1", "assistant", "안녕하세요")
    rows = conv.get_recent_messages("c1")
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "안녕"
    assert rows[0]["id"] and rows[0]["ts"]


def test_recent_is_scoped_per_chat():
    conv.add_message("c1", "user", "one")
    conv.add_message("c2", "user", "two")
    assert [r["content"] for r in conv.get_recent_messages("c1")] == ["one"]
    assert [r["content"] for r in conv.get_recent_messages("c2")] == ["two"]


def test_recent_limit_and_order():
    for i in range(15):
        conv.add_message("c1", "user", str(i), ts=f"2026-05-05T00:00:{i:02d}")
    rows = conv.get_recent_messages("c1", n=10)
    assert len(rows) == 10
    assert rows[0]["content"] == "5"  # oldest of the last 10
    assert rows[-1]["content"] == "14"


def test_admin_kind_excluded_from_recent_by_default():
    conv.add_message("c1", "user", "무시: X")
    conv.add_message("c1", "assistant", "ignore.md updated", kind="admin")
    rows = conv.get_recent_messages("c1")
    assert [r["role"] for r in rows] == ["user"]
    # but still retrievable when asked for explicitly
    both = conv.get_recent_messages("c1", kinds=("chat", "admin"))
    assert len(both) == 2


def test_reply_edge_and_thread_walk():
    q1 = conv.add_message("c1", "user", "질문1")
    a1 = conv.add_message("c1", "assistant", "답1", reply_to_id=q1)
    q2 = conv.add_message("c1", "user", "질문2", reply_to_id=a1)
    a2 = conv.add_message("c1", "assistant", "답2", reply_to_id=q2)
    thread = conv.get_thread(a2)
    assert [t["id"] for t in thread] == [q1, a1, q2, a2]


def test_thread_of_root_is_singleton():
    q1 = conv.add_message("c1", "user", "hi")
    assert [t["id"] for t in conv.get_thread(q1)] == [q1]


def test_out_of_order_pairing_via_reply_id():
    # Two user messages arrive before either is answered — line order alone
    # cannot pair them, but reply_to_id can.
    q1 = conv.add_message("c1", "user", "first question", ts="2026-05-05T00:00:00")
    q2 = conv.add_message("c1", "user", "second question", ts="2026-05-05T00:00:01")
    a2 = conv.add_message("c1", "assistant", "answer to second",
                          reply_to_id=q2, ts="2026-05-05T00:00:02")
    a1 = conv.add_message("c1", "assistant", "answer to first",
                          reply_to_id=q1, ts="2026-05-05T00:00:03")
    assert conv.get_message(a1)["reply_to_id"] == q1
    assert conv.get_message(a2)["reply_to_id"] == q2


def test_search_korean_substring():
    conv.add_message("c1", "user", "엔비디아 실적 발표 어땠어")
    conv.add_message("c1", "assistant", "SPX가 올랐다")
    hits = conv.search_messages("엔비디아")
    assert len(hits) == 1
    assert "엔비디아" in hits[0]["content"]


def test_search_english_and_scope_and_since():
    conv.add_message("c1", "user", "NVIDIA earnings", ts="2026-05-01T00:00:00")
    conv.add_message("c2", "user", "NVIDIA guidance", ts="2026-06-01T00:00:00")
    assert len(conv.search_messages("NVIDIA")) == 2
    assert len(conv.search_messages("NVIDIA", chat_id="c1")) == 1
    assert len(conv.search_messages("NVIDIA", since="2026-05-15")) == 1


def test_search_short_keyword_falls_back_to_like():
    conv.add_message("c1", "user", "AI")
    assert len(conv.search_messages("AI")) == 1


def test_search_short_keyword_escapes_like_wildcards():
    # A short keyword of "%" must be matched literally, not as "match everything".
    conv.add_message("c1", "user", "hi")
    conv.add_message("c1", "user", "50% 상승")
    hits = conv.search_messages("%")                # only the message with a literal %
    assert len(hits) == 1 and "50%" in hits[0]["content"]
    # "_" likewise literal: matches only content containing an underscore.
    conv.add_message("c1", "user", "a_b snippet")
    assert [h["content"] for h in conv.search_messages("_")] == ["a_b snippet"]


def test_get_messages_batch_in_order():
    a = conv.add_message("c1", "user", "a")
    b = conv.add_message("c1", "assistant", "b")
    rows = conv.get_messages([b, a, "nonexistent"])
    assert [r["content"] for r in rows] == ["b", "a"]
    assert conv.get_messages([]) == []


def test_import_message_is_idempotent():
    assert conv.import_message("fixed-id", "c1", "user", "hi", "2026-07-01T00:00:00+00:00")
    assert not conv.import_message("fixed-id", "c1", "user", "hi", "2026-07-01T00:00:00+00:00")
    assert len(conv.get_recent_messages("c1")) == 1


def test_search_updates_after_delete():
    conv.add_message("c1", "user", "삭제될 메시지 내용")
    assert conv.search_messages("삭제될")
    conv.clear_chat("c1")
    assert conv.search_messages("삭제될") == []


def test_clear_chat_scoped_and_all():
    conv.add_message("c1", "user", "a")
    conv.add_message("c2", "user", "b")
    assert conv.clear_chat("c1") == 1
    assert conv.get_recent_messages("c1") == []
    assert len(conv.get_recent_messages("c2")) == 1
    conv.add_message("c1", "user", "c")
    removed = conv.clear_chat()
    assert removed == 2


def test_meta_roundtrip():
    conv.add_message("c1", "user", "x", meta={"source": "telegram", "n": 3})
    row = conv.get_recent_messages("c1")[0]
    assert row["meta"] == {"source": "telegram", "n": 3}


def test_iter_all_messages_ordered():
    conv.add_message("c2", "user", "b", ts="2026-05-05T00:00:01")
    conv.add_message("c1", "user", "a", ts="2026-05-05T00:00:00")
    contents = [m["content"] for m in conv.iter_all_messages()]
    assert contents == ["a", "b"]
