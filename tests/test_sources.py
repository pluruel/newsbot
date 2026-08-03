import textwrap
from pathlib import Path
from newsparser.collector.sources import load_sources, Source


def test_load_sources_parses_table(tmp_path):
    md = textwrap.dedent("""\
        # Sources

        | Name | RSS URL | Tier | Category | Paywall |
        |------|---------|------|----------|---------|
        | Reuters | https://feeds.reuters.com/reuters/topNews | international | markets | no |
        | Financial Times | https://www.ft.com/rss/home | international | markets | yes |
    """)
    p = tmp_path / "sources.md"
    p.write_text(md)
    sources = load_sources(str(p))
    assert len(sources) == 2
    assert sources[0].name == "Reuters"
    assert sources[0].rss_url == "https://feeds.reuters.com/reuters/topNews"
    assert sources[0].tier == "international"
    assert sources[0].category == "markets"
    assert sources[0].paywall is False
    assert sources[1].paywall is True


def test_load_sources_blank_category_is_none(tmp_path):
    md = textwrap.dedent("""\
        | Name | RSS URL | Tier | Category | Paywall |
        |------|---------|------|----------|---------|
        | Hacker News | https://news.ycombinator.com/rss | tech |  | no |
    """)
    p = tmp_path / "sources.md"
    p.write_text(md)
    sources = load_sources(str(p))
    assert len(sources) == 1
    assert sources[0].category is None


def test_load_sources_skips_separator_rows(tmp_path):
    md = textwrap.dedent("""\
        | Name | RSS URL | Tier | Category | Paywall |
        |------|---------|------|----------|---------|
        | Reuters | https://feeds.reuters.com/reuters/topNews | international | markets | no |
    """)
    p = tmp_path / "sources.md"
    p.write_text(md)
    sources = load_sources(str(p))
    assert len(sources) == 1


def test_load_sources_handles_legacy_layout_without_category(tmp_path):
    """If the Category column is absent, sources still load with category=None."""
    md = textwrap.dedent("""\
        | Name | RSS URL | Tier | Paywall |
        |------|---------|------|---------|
        | Reuters | https://feeds.reuters.com/reuters/topNews | international | no |
    """)
    p = tmp_path / "sources.md"
    p.write_text(md)
    sources = load_sources(str(p))
    assert len(sources) == 1
    assert sources[0].category is None


def test_poll_source_passes_category_to_insert():
    from unittest.mock import patch, MagicMock
    from newsparser.collector.poller import poll_source

    fake_entry = MagicMock()
    fake_entry.id = "guid-x"
    fake_entry.link = "https://example.com/x"
    fake_entry.title = "Hello"
    fake_entry.published = "2026-05-07T00:00:00"
    fake_entry.published_parsed = None
    fake_entry.updated_parsed = None
    fake_entry.summary = "summary"

    fake_feed = MagicMock()
    fake_feed.entries = [fake_entry]

    src = Source(name="OpenAI Blog", rss_url="https://openai.com/rss",
                 tier="international", paywall=False, category="tech")

    with patch("newsparser.collector.poller._fetch_feed", return_value=b""), \
         patch("newsparser.collector.poller.feedparser.parse", return_value=fake_feed), \
         patch("newsparser.collector.poller.fetch_body", return_value="body"), \
         patch("newsparser.collector.poller.insert_article") as mock_insert:
        poll_source(src)

    mock_insert.assert_called_once()
    _, kwargs = mock_insert.call_args
    args = mock_insert.call_args[0]
    assert "category" in mock_insert.call_args.kwargs or len(args) >= 7
    if "category" in mock_insert.call_args.kwargs:
        assert mock_insert.call_args.kwargs["category"] == "tech"
    else:
        assert args[6] == "tech"


def test_poll_source_skips_stale_backfill():
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch, MagicMock
    from newsparser.collector.poller import poll_source

    def entry(guid, published_dt):
        e = MagicMock()
        e.id = guid
        e.link = f"https://example.com/{guid}"
        e.title = guid
        e.published = published_dt.isoformat()
        e.published_parsed = published_dt.utctimetuple()
        e.updated_parsed = None
        e.summary = "summary"
        return e

    now = datetime.now(timezone.utc)
    stale = entry("guid-old", now - timedelta(days=30))
    fresh = entry("guid-new", now - timedelta(hours=1))

    fake_feed = MagicMock()
    fake_feed.entries = [stale, fresh]

    src = Source(name="OpenAI Blog", rss_url="https://openai.com/news/rss.xml",
                 tier="international", paywall=False, category="tech")

    with patch("newsparser.collector.poller._fetch_feed", return_value=b""), \
         patch("newsparser.collector.poller.feedparser.parse", return_value=fake_feed), \
         patch("newsparser.collector.poller.fetch_body", return_value="body") as mock_fetch, \
         patch("newsparser.collector.poller.insert_article") as mock_insert, \
         patch("newsparser.collector.poller.is_seen", return_value=False), \
         patch("newsparser.collector.poller.mark_seen") as mock_seen:
        new = poll_source(src)

    assert [a["guid"] for a in new] == ["guid-new"]
    mock_insert.assert_called_once()
    assert mock_insert.call_args[0][0] == "guid-new"
    # stale entry is marked seen but never scraped
    assert mock_seen.call_args_list[0][0][0] == "guid-old"
    mock_fetch.assert_called_once_with("https://example.com/guid-new")


def test_poll_source_records_feed_health():
    from unittest.mock import patch, MagicMock
    from newsparser.collector.poller import poll_source
    from newsparser.store.sqlite import get_failing_feeds

    src = Source(name="중앙일보", rss_url="https://rss.joins.com/dead.xml",
                 tier="domestic", paywall=False, category="markets")

    # fetch 실패 → failure 기록
    with patch("newsparser.collector.poller._fetch_feed", side_effect=OSError("boom")):
        assert poll_source(src) == []
    # 200이지만 엔트리 0건 → failure 기록
    empty = MagicMock()
    empty.entries = []
    with patch("newsparser.collector.poller._fetch_feed", return_value=b""), \
         patch("newsparser.collector.poller.feedparser.parse", return_value=empty):
        assert poll_source(src) == []
    assert get_failing_feeds(min_consecutive=2)[0]["source"] == "중앙일보"

    # 엔트리가 돌아오면 카운터 리셋
    entry = MagicMock()
    entry.id = "g1"
    entry.link = "https://example.com/g1"
    entry.title = "t"
    entry.published = "2026-08-03T00:00:00"
    entry.published_parsed = None
    entry.updated_parsed = None
    entry.summary = "s"
    ok = MagicMock()
    ok.entries = [entry]
    with patch("newsparser.collector.poller._fetch_feed", return_value=b""), \
         patch("newsparser.collector.poller.feedparser.parse", return_value=ok), \
         patch("newsparser.collector.poller.fetch_body", return_value="body"), \
         patch("newsparser.collector.poller.insert_article"):
        poll_source(src)
    assert get_failing_feeds(min_consecutive=1) == []
