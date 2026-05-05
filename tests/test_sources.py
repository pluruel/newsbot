import textwrap
from pathlib import Path
from newsparser.collector.sources import load_sources, Source

def test_load_sources_parses_table(tmp_path):
    md = textwrap.dedent("""\
        # Sources

        | Name | RSS URL | Tier | Paywall |
        |------|---------|------|---------|
        | Reuters | https://feeds.reuters.com/reuters/topNews | international | no |
        | Financial Times | https://www.ft.com/rss/home | international | yes |
    """)
    p = tmp_path / "sources.md"
    p.write_text(md)
    sources = load_sources(str(p))
    assert len(sources) == 2
    assert sources[0].name == "Reuters"
    assert sources[0].rss_url == "https://feeds.reuters.com/reuters/topNews"
    assert sources[0].tier == "international"
    assert sources[0].paywall is False
    assert sources[1].name == "Financial Times"
    assert sources[1].paywall is True

def test_load_sources_skips_header_rows(tmp_path):
    md = textwrap.dedent("""\
        | Name | RSS URL | Tier | Paywall |
        |------|---------|------|---------|
        | Reuters | https://feeds.reuters.com/reuters/topNews | international | no |
    """)
    p = tmp_path / "sources.md"
    p.write_text(md)
    sources = load_sources(str(p))
    assert len(sources) == 1
