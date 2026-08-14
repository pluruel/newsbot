from datetime import datetime, timezone

from newsparser.store.sqlite import (
    init_db, is_seen, mark_seen, insert_article, get_unprocessed,
    mark_processed, mark_alerted, get_unclassified, update_category,
    get_between, _connection,
)

def test_init_db_creates_tables():
    # 두 번 호출해도 오류 없어야 함
    init_db()

def test_is_seen_false_for_new_guid():
    assert is_seen("guid-1") is False

def test_mark_seen_and_is_seen():
    mark_seen("guid-1")
    assert is_seen("guid-1") is True

def test_insert_article_and_get_unprocessed():
    insert_article("guid-1", "Reuters", "Test Title", "https://example.com", "2026-05-05T00:00:00", "body text")
    rows = get_unprocessed()
    assert len(rows) == 1
    assert rows[0]["guid"] == "guid-1"
    assert rows[0]["source"] == "Reuters"
    assert rows[0]["processed"] == 0

def test_insert_duplicate_is_ignored():
    insert_article("guid-1", "Reuters", "Title", "https://example.com", None, "body")
    insert_article("guid-1", "Reuters", "Title", "https://example.com", None, "body")
    assert len(get_unprocessed()) == 1

def test_mark_processed():
    insert_article("guid-1", "Reuters", "Title", "https://example.com", None, "body")
    insert_article("guid-2", "Reuters", "Title 2", "https://example2.com", None, "body")
    mark_processed(["guid-1"])
    rows = get_unprocessed()
    assert len(rows) == 1
    assert rows[0]["guid"] == "guid-2"

def test_mark_alerted():
    insert_article("guid-1", "Reuters", "Title", "https://example.com", None, "body")
    mark_alerted("guid-1")
    import sqlite3, os
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        row = conn.execute("SELECT alerted FROM pending_articles WHERE guid = ?", ("guid-1",)).fetchone()
    assert row[0] == 1


def test_init_db_adds_category_column_idempotent():
    # init_db is idempotent — running twice should not raise
    init_db()
    init_db()
    import sqlite3, os
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(pending_articles)").fetchall()]
    assert "category" in cols


def test_insert_article_with_category():
    insert_article("g1", "Reuters", "Title", "https://x.com", None, "body", category="markets")
    rows = get_unprocessed()
    assert rows[0]["category"] == "markets"


def test_insert_article_default_category_is_null():
    insert_article("g1", "HN", "Title", "https://x.com", None, "body")
    rows = get_unprocessed()
    assert rows[0]["category"] is None


def test_get_unprocessed_filters_by_category():
    insert_article("g1", "S1", "T1", "https://x.com/1", None, "b", category="tech")
    insert_article("g2", "S2", "T2", "https://x.com/2", None, "b", category="markets")
    insert_article("g3", "S3", "T3", "https://x.com/3", None, "b")  # NULL
    tech = get_unprocessed(category="tech")
    assert len(tech) == 1
    assert tech[0]["guid"] == "g1"
    markets = get_unprocessed(category="markets")
    assert len(markets) == 1
    assert markets[0]["guid"] == "g2"


def test_get_unprocessed_orders_mixed_date_formats_chronologically():
    """`published` is stored raw, so ISO and RFC-822 sources coexist. A string
    sort groups by format (digits before weekday names), starving RFC-822
    sources behind the entire ISO backlog — order must follow the parsed date."""
    insert_article("iso-late", "연합인포맥스", "T1", "u1", "2026-07-05 11:58:29", "b")
    insert_article("rfc-early", "Bloomberg Markets", "T2", "u2",
                   "Sun, 05 Jul 2026 10:17:44 GMT", "b")
    insert_article("rfc-kst", "매일경제", "T3", "u3", "Sun, 05 Jul 2026 18:02:01 +09:00", "b")
    insert_article("iso-early", "Yahoo Finance", "T4", "u4", "2026-07-05T08:00:00Z", "b")
    rows = get_unprocessed()
    assert [r["guid"] for r in rows] == ["iso-early", "rfc-kst", "rfc-early", "iso-late"]


def test_get_unprocessed_no_filter_returns_all():
    insert_article("g1", "S1", "T1", "https://x.com/1", None, "b", category="tech")
    insert_article("g2", "S2", "T2", "https://x.com/2", None, "b")
    assert len(get_unprocessed()) == 2


def test_get_unclassified_returns_only_null_category():
    insert_article("g1", "S1", "T1", "https://x.com/1", None, "b", category="tech")
    insert_article("g2", "S2", "T2", "https://x.com/2", None, "b")  # NULL
    rows = get_unclassified()
    assert len(rows) == 1
    assert rows[0]["guid"] == "g2"


def test_update_category():
    insert_article("g1", "S1", "T1", "https://x.com/1", None, "b")
    update_category("g1", "tech")
    rows = get_unprocessed()
    assert rows[0]["category"] == "tech"


def _at(guid: str, fetched_at: str, category: str = "markets") -> None:
    insert_article(guid, "S", f"T{guid}", f"https://x.com/{guid}", None, "b",
                   category=category)
    with _connection() as conn:
        conn.execute("UPDATE pending_articles SET fetched_at=? WHERE guid=?",
                     (fetched_at, guid))


def test_get_between_filters_window_and_category():
    _at("before", "2026-07-28T05:00:00+00:00")
    _at("inside", "2026-07-28T05:40:00+00:00")
    _at("after", "2026-07-28T06:30:00+00:00")
    _at("wrongcat", "2026-07-28T05:45:00+00:00", category="tech")
    rows = get_between(
        datetime(2026, 7, 28, 5, 30, tzinfo=timezone.utc),
        datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc),
        category="markets",
    )
    assert [r["guid"] for r in rows] == ["inside"]


def test_get_between_ignores_processed_flag():
    _at("g1", "2026-07-28T05:40:00+00:00")
    mark_processed(["g1"])
    rows = get_between(
        datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc),
    )
    assert [r["guid"] for r in rows] == ["g1"]


def test_get_between_matches_naive_legacy_timestamps():
    """Pre-migration rows stored fetched_at without a UTC offset; both forms are
    UTC and compare correctly on their shared prefix."""
    _at("legacy", "2026-07-28T05:40:00")
    rows = get_between(
        datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc),
    )
    assert [r["guid"] for r in rows] == ["legacy"]


def test_get_between_caps_to_newest_rows_oldest_first():
    """Truncation must drop the *oldest* rows: in a burst the headlines nearest
    the move are the ones headlines.candidates needs to see."""
    for i in range(5):
        _at(f"g{i}", f"2026-07-28T05:4{i}:00+00:00")
    rows = get_between(
        datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc),
        limit=3,
    )
    assert [r["guid"] for r in rows] == ["g2", "g3", "g4"]


def test_feed_health_roundtrip():
    from newsparser.store.sqlite import get_failing_feeds, record_feed_failure, record_feed_ok

    for _ in range(3):
        record_feed_failure("중앙일보", "HTTP 404")
    record_feed_failure("한겨레", "timeout")
    record_feed_ok("매일경제")

    failing = get_failing_feeds(min_consecutive=3)
    assert [r["source"] for r in failing] == ["중앙일보"]
    assert failing[0]["consecutive_failures"] == 3
    assert failing[0]["last_error"] == "HTTP 404"

    # 성공하면 카운터 리셋
    record_feed_ok("중앙일보")
    assert get_failing_feeds(min_consecutive=1) == [{"source": "한겨레", "last_ok": None,
                                                    "consecutive_failures": 1, "last_error": "timeout"}]
    row = [r for r in get_failing_feeds(min_consecutive=0) if r["source"] == "중앙일보"][0]
    assert row["consecutive_failures"] == 0
    assert row["last_ok"] is not None
