import textwrap

from newsparser.collector.html_index import parse_index

BASE = "https://www.anthropic.com/news"

# The /news list layout: the post link wraps the heading with presentational
# spans in between, and the tile also carries a taxonomy link to a section page
# that looks just like a post URL.
LIST_TILE = """\
<li class="bxgyUW_row" data-listing-row="0c98">
  <div class="bxgyUW_title">
    <a class="bxgyUW_title-link" href="/news/s1-sec">
      <span class="panning"><span class="nowrap"><h3 class="font-medium">Anthropic files S-1 with the SEC</h3></span></span>
    </a>
  </div>
  <div class="bxgyUW_description">
    <a tabindex="-1" href="/news/s1-sec"><span><p>Today, Anthropic, PBC filed a registration statement.</p></span></a>
  </div>
  <div class="bxgyUW_category"><a class="wN10Da_link" href="/news/announcements">Announcements</a></div>
  <div class="bxgyUW_date"><time>Oct 26, 2026</time></div>
</li>"""

# The /research grid layout: date first, a long <figure> in between, and the
# post link nested inside the heading.
GRID_TILE = """\
<article class="nUeDbq_article" data-listing-row="b951">
  <header><p><a class="wN10Da_link" href="/engineering">Engineering</a></p><time>Nov 6, 2026</time></header>
  <figure><img alt="Collage of two offset half circles" src="/_next/image?url=x&amp;w=1920"/></figure>
  <div class="nUeDbq_meta">
    <h3 class="headline-6"><a class="nUeDbq_metaTitleLink" href="/research/components-of-agent-memory">The components of agent memory</a></h3>
    <p class="nUeDbq_metaDescription">For Claude to learn from experience, it has to decide what&#x27;s worth remembering.</p>
  </div>
</article>"""


def _page(*tiles: str) -> str:
    return textwrap.dedent("""\
        <html><body>
          <nav><a href="/news">News</a><a href="/research">Research</a></nav>
          %s
        </body></html>""") % "\n".join(tiles)


def test_parses_both_tile_layouts():
    entries = parse_index(_page(LIST_TILE, GRID_TILE), BASE)
    assert [e.link for e in entries] == [
        "https://www.anthropic.com/news/s1-sec",
        "https://www.anthropic.com/research/components-of-agent-memory",
    ]
    assert entries[0].title == "Anthropic files S-1 with the SEC"
    assert entries[0].summary == "Today, Anthropic, PBC filed a registration statement."
    assert entries[0].published == "2026-10-26T00:00:00+00:00"
    assert entries[0].published_parsed[:3] == (2026, 10, 26)
    # guid is the post URL, which is also what a Hacker News item carries
    assert entries[0].id == entries[0].link
    assert entries[1].title == "The components of agent memory"
    assert entries[1].summary.endswith("worth remembering.")  # entity unescaped
    assert entries[1].published == "2026-11-06T00:00:00+00:00"


def test_taxonomy_link_is_not_mistaken_for_the_post():
    """The tile's section link (/news/announcements) must never become the guid."""
    entries = parse_index(_page(LIST_TILE), BASE)
    assert [e.link for e in entries] == ["https://www.anthropic.com/news/s1-sec"]


def test_featured_post_repeated_in_the_list_yields_one_entry():
    featured = LIST_TILE.replace('data-listing-row="0c98"', 'data-listing-row="hero"')
    entries = parse_index(_page(featured, LIST_TILE), BASE)
    assert len(entries) == 1


def test_post_link_outside_a_section_is_kept():
    """Model launches live at the site root (/claude-haiku-5-5), not under /news."""
    tile = LIST_TILE.replace('href="/news/s1-sec"', 'href="/claude-haiku-5-5"')
    entries = parse_index(_page(tile), BASE)
    assert [e.link for e in entries] == ["https://www.anthropic.com/claude-haiku-5-5"]


def test_query_string_is_stripped_from_the_guid():
    tile = LIST_TILE.replace('href="/news/s1-sec"', 'href="/news/s1-sec?utm_source=x"', 1)
    entries = parse_index(_page(tile), BASE)
    assert entries[0].link == "https://www.anthropic.com/news/s1-sec"


def test_unparseable_date_drops_the_tile():
    """Rather than dating it now: a layout change must not backfill the archive.
    Dropping every tile leaves no entries, which poll_source records as a feed
    failure — a visible breakage instead of a silent one."""
    tile = LIST_TILE.replace("<time>Oct 26, 2026</time>", "<time>2026-10-26</time>")
    assert parse_index(_page(tile), BASE) == []


def test_undated_tile_is_skipped():
    tile = LIST_TILE.replace("<time>Oct 26, 2026</time>", "")
    assert parse_index(_page(tile), BASE) == []


def test_page_without_tiles_yields_no_entries():
    assert parse_index("<html><body><p>nothing here</p></body></html>", BASE) == []
