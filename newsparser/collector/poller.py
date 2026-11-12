import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests

from newsparser.collector.html_index import parse_index
from newsparser.collector.scraper import fetch_body
from newsparser.collector.sources import Source
from newsparser.store.sqlite import (
    insert_article,
    is_seen,
    mark_seen,
    record_feed_failure,
    record_feed_ok,
)

logger = logging.getLogger(__name__)

# Some feeds (hankyung.com) 403 feedparser's default agent and urllib itself,
# so fetch with requests and hand the bytes to feedparser. Keep this exact UA:
# hankyung's WAF also rejects full Chrome UA strings but accepts this stub.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

# news.ycombinator.com wants the opposite of hankyung: any browser-like UA gets
# a 6-byte "Sorry\n" body with HTTP 419, while a plain descriptive agent is
# served the feed. Per-host override keyed on the URL netloc.
UA_OVERRIDES = {
    "news.ycombinator.com": "newsbot/1.0 (personal RSS reader)",
}

# A newly added feed can carry a deep archive (openai.com/news/rss.xml ships
# 1,100+ entries) — entries older than this are marked seen without being
# inserted, so they never reach a /cycle or get body-scraped.
MAX_AGE_DAYS = 7


def _is_stale(entry) -> bool:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return False
    published = datetime(*parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - published > timedelta(days=MAX_AGE_DAYS)


def _fetch_feed(url: str) -> bytes:
    agent = UA_OVERRIDES.get(urlparse(url).netloc, USER_AGENT)
    resp = requests.get(url, headers={"User-Agent": agent}, timeout=30)
    resp.raise_for_status()
    return resp.content


def _entries(source: Source) -> list:
    """Entries for a source, whichever shape it publishes in.

    An html-index source is a publisher with no feed: its listing page is parsed
    into the same entry shape, so everything below this call is identical for
    both kinds.
    """
    raw = _fetch_feed(source.rss_url)
    if source.kind == "html-index":
        # Listing pages are utf-8; a replacement char in one title beats losing
        # the whole page to a decode error.
        return parse_index(raw.decode("utf-8", "replace"), source.rss_url)
    return feedparser.parse(raw).entries


def poll_source(source: Source) -> list[dict]:
    """Fetch a source and store new articles. Returns list of new article dicts."""
    try:
        entries = _entries(source)
    except Exception as exc:
        logger.error("Fetch failed for %s: %s", source.name, exc)
        record_feed_failure(source.name, str(exc))
        return []

    if not entries:
        # A 200 that isn't a feed anymore (rss.joins.com) is as dead as a 404 —
        # as is a listing page whose markup stopped yielding tiles.
        logger.warning("Feed %s returned no entries", source.name)
        record_feed_failure(source.name, "feed has no entries")
        return []
    record_feed_ok(source.name)

    new_articles = []
    for entry in entries:
        guid = getattr(entry, "id", None) or getattr(entry, "link", None)
        if not guid or is_seen(guid):
            continue

        if _is_stale(entry):
            mark_seen(guid)
            continue

        title = getattr(entry, "title", "")
        url = getattr(entry, "link", "")
        published = getattr(entry, "published", datetime.now(timezone.utc).isoformat())
        summary = getattr(entry, "summary", "")

        if source.paywall:
            body = summary
        else:
            body = fetch_body(url) or summary

        insert_article(guid, source.name, title, url, published, body,
                       category=source.category)
        mark_seen(guid)
        new_articles.append({"guid": guid, "source": source.name, "title": title, "fetched_at": datetime.now(timezone.utc).isoformat()})
        logger.info("New article: [%s] %s", source.name, title)

    return new_articles


def poll_all(sources: list[Source]) -> list[dict]:
    """Poll all sources and return combined list of new articles."""
    results = []
    for source in sources:
        results.extend(poll_source(source))
    return results
