import logging
import os
from pathlib import Path

from newsparser.bot.sender import send_message
from newsparser.claude.runner import run_claude
from newsparser.scheduler.interests import interests_rollup
from newsparser.scheduler.workspace import ensure_workspace

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


def run_morning(date_str: str) -> None:
    """Compose and send the daily brief."""
    try:
        interests_rollup()
    except Exception as e:
        logger.warning("Interest rollup failed (%s: %s) — brief will use current interests.md", type(e).__name__, e)

    workspace = ensure_workspace()

    # Collect 4 most recent cycle files
    cycle_files = sorted((workspace / "cycles").glob("*.md"))[-4:]
    cycle_paths = "\n".join(str(p) for p in cycle_files)

    prompt = (
        "/morning\n\n"
        f"Date: {date_str}\n"
        f"Recent cycle files:\n{cycle_paths}\n"
        f"Interests: {workspace / 'me' / 'interests.md'}\n"
        f"Manifesto: {workspace / 'me' / 'manifesto.md'}"
    )

    brief = run_claude(prompt)

    # Send to Telegram with up to 3 retries
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            send_message(brief)
            break
        except Exception as e:
            logger.warning("Telegram send attempt %d failed: %s", attempt, e)
            if attempt == MAX_RETRIES:
                logger.error("All Telegram retries failed. Brief saved locally.")

    # Save locally
    brief_path = workspace / "briefs" / f"{date_str}.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(brief, encoding="utf-8")
    logger.info("Brief saved: %s", brief_path)
