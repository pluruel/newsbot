"""Report-file helpers that build the context for the API-based scheduled jobs."""

import logging
from collections.abc import Sequence
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_GRAPH_HEADING = "## Graph updates"
_TRIAGE_PREFIX = "트리아지:"


def strip_graph_block(text: str) -> str:
    """Drop the ``## Graph updates`` block, keeping the trailing triage stats line.

    run_cycle appends ``트리아지: …`` after the graph block; reflect reads it to
    tell gating from declining relevance, so it must survive the strip.
    """
    head, sep, tail = text.partition(_GRAPH_HEADING)
    if not sep:
        return text
    kept = [ln for ln in tail.splitlines() if ln.startswith(_TRIAGE_PREFIX)]
    digest = head.rstrip()
    if not kept:
        return digest + "\n"
    return digest + "\n\n" + "\n".join(kept) + "\n"


def previous_report(workspace: Path, category: str, slot: str) -> Path | None:
    """Latest ``cycles/{category}`` report whose slot sorts before ``slot``."""
    folder = workspace / "cycles" / category
    # Stems are YYYY-MM-DD-HH, so lexicographic order is chronological.
    earlier = [p for p in folder.glob("*.md") if p.stem < slot]
    return max(earlier, key=lambda p: p.stem, default=None)


def reports_between(
    workspace: Path,
    categories: Sequence[str],
    start: date,
    end: date,
    # Korean runs ~0.7-1 token/char, so 600k chars keeps reflect's 14-day window
    # clear of the 1M-token context.
    max_chars: int = 600_000,
) -> list[tuple[str, str]]:
    """``(category/stem, digest)`` pairs dated ``start..end`` inclusive, oldest first.

    Over ``max_chars`` in total, the oldest reports are dropped (with a warning)
    so the newest context always fits the request.
    """
    found: list[tuple[str, str, Path]] = []
    for category in categories:
        for path in (workspace / "cycles" / category).glob("*.md"):
            try:
                d = date.fromisoformat(path.stem[:10])
            except ValueError:
                continue
            if start <= d <= end:
                found.append((path.stem, f"{category}/{path.stem}", path))
    found.sort(key=lambda t: (t[0], t[1]))

    items = [
        (key, strip_graph_block(path.read_text(encoding="utf-8")))
        for _, key, path in found
    ]
    total = sum(len(text) for _, text in items)
    dropped = 0
    while items and total > max_chars:
        total -= len(items.pop(0)[1])
        dropped += 1
    if dropped:
        logger.warning(
            "reports_between: dropped %d oldest report(s) to fit max_chars=%d",
            dropped, max_chars,
        )
    return items


def format_report_blocks(reports: Sequence[tuple[str, str]]) -> str:
    """``<report name="...">`` items for a ``<reports>`` prompt block (weekly, reflect)."""
    return "\n\n".join(f'<report name="{name}">\n{text}\n</report>' for name, text in reports)
