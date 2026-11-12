import logging
from dataclasses import dataclass
from pathlib import Path

from newsparser._mdtable import parse_rows

logger = logging.getLogger(__name__)

# How a source's URL is read: a feed parsed by feedparser, or a publisher's HTML
# listing page scraped into entries (collector/html_index.py) because it ships
# no feed at all. Blank in the table means "rss", so legacy rows keep working.
SOURCE_KINDS = ("rss", "html-index")


@dataclass
class Source:
    name: str
    rss_url: str
    tier: str
    paywall: bool = False
    category: str | None = None
    kind: str = "rss"


def load_sources(path: str = "sources.md") -> list[Source]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"sources file not found: {path!r}") from None

    sources: list[Source] = []
    for row in parse_rows(text):
        rss_url = row.get("rss url", "")
        if not rss_url.startswith("http"):
            logger.warning("Skipping source row with non-HTTP URL: %r", rss_url)
            continue

        kind = row.get("kind") or "rss"  # blank cell -> plain RSS
        if kind not in SOURCE_KINDS:
            logger.warning("Skipping source %r with unknown kind: %r",
                           row.get("name", ""), kind)
            continue

        category = row.get("category") or None  # blank cell -> None
        sources.append(Source(
            name=row.get("name", ""),
            rss_url=rss_url,
            tier=row.get("tier", ""),
            paywall=row.get("paywall", "").lower() == "yes",
            category=category,
            kind=kind,
        ))
    return sources
