import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")

from newsparser.bot.sender import send_long_message
from newsparser.claude.input_builder import build_input_file
from newsparser.claude.output_parser import parse_graph_updates
from newsparser.claude.runner import run_claude
from newsparser.graph.writer import apply_graph_updates
from newsparser.store.sqlite import get_unprocessed, mark_processed
from newsparser.scheduler.lock import acquire_lock, release_lock, LockError
from newsparser.scheduler.workspace import ensure_workspace

logger = logging.getLogger(__name__)

_CYCLE_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "cycle.md"


def run_cycle(slot: str) -> None:
    """Full /cycle flow: collect → claude → parse → neo4j."""
    workspace = ensure_workspace()
    lock_path = workspace / "state" / "lockfile"

    try:
        acquire_lock(lock_path)
    except LockError as e:
        logger.warning("Cycle aborted: %s", e)
        return

    try:
        unprocessed = get_unprocessed()
        if not unprocessed:
            logger.info("No unprocessed articles for slot %s", slot)
            return

        input_path = build_input_file(slot)
        logger.info("Built input file: %s (%d articles)", input_path, len(unprocessed))

        spec = _CYCLE_PROMPT_PATH.read_text(encoding="utf-8")
        prompt = f"{spec}\n\nInput file: {input_path}"
        report = run_claude(prompt)

        report_path = workspace / "cycles" / f"{slot}.md"
        report_path.write_text(report, encoding="utf-8")
        logger.info("Cycle report written: %s", report_path)

        entities, relations = parse_graph_updates(report)
        apply_graph_updates(entities, relations, cycle_id=slot)
        logger.info("Graph updated: %d entities, %d relations", len(entities), len(relations))

        digest = report.split("## Graph updates", 1)[0].rstrip()
        try:
            send_long_message(digest or report)
        except Exception as e:
            logger.error("Telegram send failed for cycle %s: %s", slot, e)

        mark_processed([a["guid"] for a in unprocessed])

        log_path = workspace / "logs" / f"{slot[:10]}.log"
        with log_path.open("a") as f:
            f.write(
                f"{datetime.now(_KST).isoformat()} cycle {slot} OK "
                f"articles={len(unprocessed)} entities={len(entities)} relations={len(relations)}\n"
            )

    finally:
        release_lock(lock_path)
