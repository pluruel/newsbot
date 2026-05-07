import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Source:
    name: str
    rss_url: str
    tier: str
    paywall: bool = False
    category: str | None = None


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into stripped cells."""
    # Drop the leading and trailing pipe before splitting so empty cells survive.
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return all(set(c) <= set("-:") and c for c in cells)


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
        cells = _split_row(line)
        if header is None:
            header = [h.lower() for h in cells]
            continue
        if _is_separator(cells):
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
