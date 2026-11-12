"""Parse a publisher's HTML listing page into feedparser-shaped entries.

For publishers that ship no feed at all. anthropic.com is the case this exists
for: as of 2026-11-12 every feed path 404s (rss.xml, news/rss.xml, feed.xml,
news/feed, research/rss.xml), so its announcements only reached us when someone
posted them to Hacker News — 17 of the 25 posts then on /news had never arrived.
The listing pages themselves carry date, title and summary per post, which is
everything an entry needs.

The entries this returns are consumed by poller.poll_source, so its dedup,
staleness and insert path stay identical to the RSS one. ``id``/``link`` are the
canonical post URL, which is also the guid Hacker News items carry — a post that
came in through HN first is therefore already `is_seen` and never duplicated.
"""
import logging
import re
from datetime import datetime, timezone
from html import unescape
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# One listing tile per <article>, or per element carrying a data-listing-row id
# (anthropic.com's /news list renders rows as <li data-listing-row>, its
# /research grid as <article data-listing-row>). Both hooks are tag names or
# data attributes, never the build-hashed class names (bxgyUW_row) beside them.
# Splitting first is what keeps each post's <time>, link and title together:
# the taxonomy link in a tile (/news/announcements) looks exactly like a post
# link, so tiles can only be told apart by their boundaries.
_TILE_SPLIT_RE = re.compile(
    r"(?=<article\b)|(?=<[a-z][a-z0-9]*\b[^>]*\bdata-listing-row=)"
)
_TIME_RE = re.compile(r"<time\b[^>]*>([^<]+)</time>")
# The post title is the tile's first <h3>; its link is the nearest href that
# wraps or sits inside that heading. Three layouts are in use — link wrapping
# the heading with <span>s in between (/news list), link inside the heading
# (/research grid), link directly around it (/news featured tile) — and that
# rule holds for all three, where a fixed nesting pattern holds for none.
_HEADING_RE = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.S)
_HREF_RE = re.compile(r'\bhref="([^"]+)"')
_SUMMARY_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
# Listing dates are rendered date-only, e.g. "Nov 9, 2026".
_DATE_FORMAT = "%b %d, %Y"


def _text(html: str) -> str:
    return unescape(_TAG_RE.sub("", html)).strip()


def _canonical(base_url: str, href: str) -> str | None:
    """Absolute, query/fragment-free URL on the index's own host, or None.

    The query string goes because the same post is linked with and without
    tracking params, and the guid has to be stable across both — it is also
    what a Hacker News item carries for the same post.
    """
    if not href or href.startswith(("#", "mailto:", "javascript:")):
        return None
    base = urlsplit(base_url)
    parts = urlsplit(urljoin(base_url, href))
    path = parts.path.rstrip("/")
    if parts.netloc != base.netloc or not path or path == base.path.rstrip("/"):
        return None
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _wraps_heading(chunk: str, href_end: int, heading_start: int) -> bool:
    """True if the anchor at `href_end` opens with only markup before the heading.

    This is what separates a tile's own link from the taxonomy link in its
    header: the post link opens and the heading follows with nothing but tags in
    between, while the taxonomy anchor spends its own text ("Announcements")
    and the tile date before the heading starts.
    """
    tag_end = chunk.find(">", href_end)
    if tag_end == -1 or tag_end > heading_start:
        return False
    return not _text(chunk[tag_end + 1:heading_start])


def _post_url(chunk: str, heading: re.Match, base_url: str) -> str | None:
    """The tile's own post URL: the link that wraps or sits inside its heading."""
    candidates = []
    for href in _HREF_RE.finditer(chunk):
        url = _canonical(base_url, href.group(1))
        if not url:
            continue
        inside_heading = heading.start() < href.start() < heading.end()
        if inside_heading or _wraps_heading(chunk, href.end(), heading.start()):
            candidates.append((abs(href.start() - heading.start()), url))
    if not candidates:
        return None
    return min(candidates)[1]


def _parse_tile(chunk: str, base_url: str) -> SimpleNamespace | None:
    heading = _HEADING_RE.search(chunk)
    time_match = _TIME_RE.search(chunk)
    if not heading or not time_match:
        return None

    title = _text(heading.group(1))
    url = _post_url(chunk, heading, base_url)
    if not url or not title:
        return None

    raw_date = _text(time_match.group(1))
    try:
        published = datetime.strptime(raw_date, _DATE_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        # Dropped rather than dated "now": a layout change that breaks the date
        # must not backfill the whole archive into the next cycle. Dropping every
        # tile leaves the page with no entries, which poll_source records as a
        # feed failure — visible in feed_health instead of silent.
        logger.warning("Unparseable listing date %r for %s", raw_date, url)
        return None

    summary_match = _SUMMARY_RE.search(chunk, heading.end())
    return SimpleNamespace(
        id=url,
        link=url,
        title=title,
        published=published.isoformat(),
        published_parsed=published.utctimetuple(),
        updated_parsed=None,
        summary=_text(summary_match.group(1)) if summary_match else "",
    )


def parse_index(html: str, base_url: str) -> list[SimpleNamespace]:
    """Entries for every post tile on a listing page, in page order.

    A post featured at the top and repeated in the list below (anthropic.com
    does this with its lead story) yields one entry, keyed on the post URL.
    """
    entries: list[SimpleNamespace] = []
    seen: set[str] = set()
    for chunk in _TILE_SPLIT_RE.split(html):
        entry = _parse_tile(chunk, base_url)
        if entry is None or entry.link in seen:
            continue
        seen.add(entry.link)
        entries.append(entry)
    return entries
