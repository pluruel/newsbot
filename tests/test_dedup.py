from datetime import datetime, timedelta, timezone

from newsparser.dedup import dedupe_pending
from newsparser.store.sqlite import (
    _connect,
    get_unprocessed,
    insert_article,
    mark_processed,
)

T0 = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)


def _insert(guid, title, *, body="x" * 100, category="markets",
            hours=0, source="src", published=None):
    insert_article(guid, source, title, f"https://e.x/{guid}",
                   published or (T0 + timedelta(hours=hours)).isoformat(), body,
                   category=category)


def _set_fetched_at(guid, iso):
    conn = _connect()
    try:
        conn.execute("UPDATE pending_articles SET fetched_at = ? WHERE guid = ?",
                     (iso, guid))
        conn.commit()
    finally:
        conn.close()


def _pending_guids(category="markets"):
    return {a["guid"] for a in get_unprocessed(category=category)}


def _rows():
    conn = _connect()
    try:
        return {r["guid"]: dict(r)
                for r in conn.execute("SELECT * FROM pending_articles").fetchall()}
    finally:
        conn.close()


def test_exact_duplicate_keeps_longest_body():
    _insert("a", "SpaceX Files for IPO on Nasdaq Under SPCX Symbol", body="short")
    _insert("b", "SpaceX Files for IPO on Nasdaq Under SPCX Symbol",
            body="much longer body " * 10, hours=2, source="other")
    assert dedupe_pending("markets") == 1
    assert _pending_guids() == {"b"}


def test_dropped_row_records_duplicate_of():
    _insert("a", "Short Seller Andrew Left Found Guilty of Securities Fraud",
            body="long body " * 20)
    _insert("b", "Short seller Andrew Left found guilty of securities fraud",
            body="stub", hours=1)
    dedupe_pending("markets")
    rows = _rows()
    assert rows["b"]["processed"] == 1
    assert rows["b"]["duplicate_of"] == "a"
    assert rows["a"]["processed"] == 0


def test_title_extension_collapsed():
    _insert("a", "Mariana Mazzucato Thinks We Need More Moonshots", body="stub")
    _insert("b", "Mariana Mazzucato Thinks We Need More Moonshots | Odd Lots",
            body="full body " * 30, hours=3)
    assert dedupe_pending("markets") == 1
    assert _pending_guids() == {"b"}


def test_similar_but_distinct_titles_survive():
    _insert("a", "Peter Neumann has died")
    _insert("b", "Peter Salus has died", hours=1)
    assert dedupe_pending("markets") == 0
    assert _pending_guids() == {"a", "b"}


def test_korean_daily_columns_survive():
    _insert("a", "5월13일 인사")
    _insert("b", "5월13일 궂긴 소식", hours=1)
    _insert("c", "5월13일 알림", hours=2)
    assert dedupe_pending("markets") == 0
    assert _pending_guids() == {"a", "b", "c"}


def test_korean_extension_collapsed():
    _insert("a", "후반기 국회의장 후보에 조정식", body="stub")
    _insert("b", "[속보] 22대 후반기 국회의장 후보에 조정식",
            body="full " * 100, hours=1)
    assert dedupe_pending("markets") == 1
    assert _pending_guids() == {"b"}


def test_date_series_survives():
    _insert("a", "US Premarket Movers for May 7, 2026")
    _insert("b", "US Premarket Movers for May 8, 2026", hours=24)
    assert dedupe_pending("markets") == 0
    assert _pending_guids() == {"a", "b"}


def test_short_titles_never_dedup():
    _insert("a", "Notes on DeepSeek")
    _insert("b", "Notes on DeepSeek", hours=1)
    assert dedupe_pending("markets") == 0


def test_outside_window_survives():
    _insert("a", "Qatar Sends First LNG Shipment Through Hormuz Since War Started")
    _insert("b", "Qatar Sends First LNG Shipment Through Hormuz Since War Started",
            hours=72)
    assert dedupe_pending("markets") == 0


def test_duplicate_of_recently_processed_dropped():
    _insert("a", "WHO Declares Ebola Outbreak a Global Health Emergency")
    mark_processed(["a"])
    _insert("b", "WHO Declares Ebola Outbreak a Global Health Emergency", hours=4)
    assert dedupe_pending("markets") == 1
    assert _pending_guids() == set()


def test_anchor_found_when_published_carries_non_utc_offset():
    """`since` must be UTC-normalized: `fetched_at` is stored as a UTC ISO
    string and SQLite compares it lexicographically, so a since carrying a
    Korean feed's +09:00 would skip anchors that are genuinely in-window."""
    title = "Samsung to Build $17B Chip Plant in Taylor, Texas"
    _insert("a", title, published="2026-08-01T08:00:00+09:00")
    mark_processed(["a"])
    # In-window in real time (since is 2026-07-30T00:00Z), but its digits sort
    # below "2026-07-30T09:00:00+09:00" — the un-normalized since.
    _set_fetched_at("a", "2026-07-30T05:00:00+00:00")
    _insert("b", title, published="2026-08-01T09:00:00+09:00")
    assert dedupe_pending("markets") == 1
    assert _pending_guids() == set()


def test_category_isolation():
    _insert("a", "OpenAI unveils its first custom chip, built by Broadcom",
            category="tech")
    _insert("b", "OpenAI unveils its first custom chip, built by Broadcom",
            category="tech", hours=1)
    assert dedupe_pending("markets") == 0
    assert _pending_guids("tech") == {"a", "b"}
    assert dedupe_pending("tech") == 1
