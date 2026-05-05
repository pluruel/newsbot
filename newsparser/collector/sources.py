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


def load_sources(path: str = "sources.md") -> list[Source]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"sources file not found: {path!r}") from None
    sources = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) < 4:
            continue
        name, rss_url, tier, paywall_str = parts[0], parts[1], parts[2], parts[3]
        if not rss_url.startswith("http"):
            logger.warning("Skipping source row with non-HTTP URL: %r", rss_url)
            continue
        sources.append(Source(
            name=name,
            rss_url=rss_url,
            tier=tier,
            paywall=paywall_str.lower() == "yes",
        ))
    return sources
