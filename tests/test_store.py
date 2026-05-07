from newsparser.store.sqlite import (
    init_db, is_seen, mark_seen, insert_article, get_unprocessed,
    mark_processed, mark_alerted, get_unclassified, update_category,
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
