import logging
from datetime import datetime

import feedparser

from newsparser.collector.scraper import fetch_body
from newsparser.collector.sources import Source
from newsparser.store.sqlite import insert_article, is_seen, mark_seen

logger = logging.getLogger(__name__)


def poll_source(source: Source) -> list[dict]:
    """Fetch RSS feed and store new articles. Returns list of new article dicts."""
    try:
        feed = feedparser.parse(source.rss_url)
    except Exception as exc:
        logger.error("RSS fetch failed for %s: %s", source.name, exc)
        return []

    new_articles = []
    for entry in feed.entries:
        guid = entry.get("id") or entry.get("link")
        if not guid or is_seen(guid):
            continue

        title = entry.get("title", "")
        url = entry.get("link", "")
        published = entry.get("published", datetime.utcnow().isoformat())

        if source.paywall:
            body = entry.get("summary", "")
        else:
            body = fetch_body(url) or entry.get("summary", "")

        insert_article(guid, source.name, title, url, published, body)
        mark_seen(guid)
        new_articles.append({"guid": guid, "source": source.name, "title": title, "fetched_at": datetime.utcnow().isoformat()})
        logger.info("New article: [%s] %s", source.name, title)

    return new_articles


def poll_all(sources: list[Source]) -> list[dict]:
    """Poll all sources and return combined list of new articles."""
    results = []
    for source in sources:
        results.extend(poll_source(source))
    return results
