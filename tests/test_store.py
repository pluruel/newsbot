from newsparser.store.sqlite import init_db, is_seen, mark_seen, insert_article, get_unprocessed, mark_processed, mark_alerted

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
