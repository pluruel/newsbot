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
