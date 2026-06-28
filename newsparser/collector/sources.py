import logging
from dataclasses import dataclass
from pathlib import Path

from newsparser._mdtable import split_row, is_separator

logger = logging.getLogger(__name__)


@dataclass
class Source:
    name: str
    rss_url: str
    tier: str
    paywall: bool = False
    category: str | None = None


def load_sources(path: str = "sources.md") -> list[Source]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"sources file not found: {path!r}") from None

    header: list[str] | None = None
    sources: list[Source] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = split_row(line)
        if header is None:
            header = [h.lower() for h in cells]
            continue
        if is_separator(cells):
            continue
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))

        row = dict(zip(header, cells))
        rss_url = row.get("rss url", "")
        if not rss_url.startswith("http"):
            logger.warning("Skipping source row with non-HTTP URL: %r", rss_url)
            continue

        category = row.get("category") or None  # blank cell -> None
        sources.append(Source(
            name=row.get("name", ""),
            rss_url=rss_url,
            tier=row.get("tier", ""),
            paywall=row.get("paywall", "").lower() == "yes",
            category=category,
        ))
    return sources
