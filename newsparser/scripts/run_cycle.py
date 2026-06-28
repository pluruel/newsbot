# newsparser/scripts/run_cycle.py
import logging
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from newsparser.bot.sender import send_long_message
from newsparser.claude.input_builder import build_input_file
from newsparser.claude.runner import run_claude
from newsparser.classifier import classify_article, CATEGORIES
from newsparser.market import snapshot as market_snapshot
from newsparser.market import store as market_store
from newsparser.store.sqlite import get_unclassified, get_unprocessed, mark_processed, update_category
from newsparser.scheduler.workspace import ensure_workspace

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_GRAPH_UPDATES_HEADER = "## Graph updates"
_SECTION_HEADERS = {"새 소식", "이어지는 흐름", "조용한 영역", "오픈 스레드"}


def _terse_from_file(report_text: str) -> str:
    """Extract section headers and bullet lines from a verbose report file."""
    lines = [
        line.strip()
        for line in report_text.split("\n")
        if line.strip() in _SECTION_HEADERS or line.strip().startswith("•")
    ]
    return "\n".join(lines)


def _classify_pending() -> None:
    rows = get_unclassified()
    if not rows:
        return
    logger.info("Classifying %d untagged articles", len(rows))
    for r in rows:
        try:
            cat = classify_article(r["title"], r["body"])
        except Exception as exc:
            logger.warning("Classifier error on %s: %s — defaulting to markets", r["guid"], exc)
            cat = "markets"
        update_category(r["guid"], cat)


def _run_for_category(slot: str, category: str, workspace: Path) -> None:
    articles = get_unprocessed(category=category)
    if not articles:
        logger.info("No unprocessed articles for category=%s slot=%s", category, slot)
        return

    guids_path = workspace / "input" / category / f"{slot}-guids.txt"
    guids_path.parent.mkdir(parents=True, exist_ok=True)
    if guids_path.exists():
        logger.warning("[%s] stale guids file found — prior cycle may have crashed", category)
    guids_path.write_text("\n".join(a["guid"] for a in articles), encoding="utf-8")

    build_input_file(slot, category)
    logger.info("[%s] Built input file (%d articles)", category, len(articles))

    # Prepend a market snapshot block to the input file so Claude sees it first.
    input_path = workspace / "input" / category / f"{slot}-input.md"
    try:
        market_store.init_market_db()
        slot_date = date.fromisoformat(slot[:10])
        snapshot_block = market_snapshot.build_snapshot_block(slot_date)
    except Exception as exc:
        logger.warning("[%s] market snapshot failed: %s", category, exc)
        snapshot_block = ""
    if snapshot_block and input_path.exists():
        existing = input_path.read_text(encoding="utf-8")
        input_path.write_text(snapshot_block + "\n\n" + existing, encoding="utf-8")

    try:
        summary = run_claude(f"/cycle {slot} {category}")
        logger.info("[%s] Claude cycle complete", category)
    except Exception:
        if guids_path.exists():
            guids_path.unlink()
        raise

    # Safety net: if the slash command's mark_processed.py call was skipped or failed,
    # the guids file still exists. Mark them here to prevent reprocessing on next cycle.
    if guids_path.exists():
        logger.warning("[%s] guids file still present after run_claude — marking processed directly", category)
        guids = [g for g in guids_path.read_text(encoding="utf-8").splitlines() if g.strip()]
        if guids:
            mark_processed(guids)
        guids_path.unlink()

    # Telegram gets the terse keyword summary the skill prints to stdout. Strip the graph
    # block defensively in case the LLM included it. Fall back to terse extraction from the
    # report file only if stdout is empty.
    report_path = workspace / "cycles" / category / f"{slot}.md"
    summary = summary.strip()
    if _GRAPH_UPDATES_HEADER in summary:
        summary = summary.split(_GRAPH_UPDATES_HEADER, 1)[0].strip()
    if not summary and report_path.exists():
        logger.warning("[%s] empty cycle stdout — falling back to file digest", category)
        report = report_path.read_text(encoding="utf-8")
        summary = _terse_from_file(report.split(_GRAPH_UPDATES_HEADER, 1)[0])
    if summary:
        try:
            send_long_message(f"[{category.upper()}] {summary}")
        except Exception as e:
            logger.error("Telegram send failed for %s/%s: %s", category, slot, e)
    else:
        logger.warning("[%s] cycle produced no summary for slot=%s", category, slot)

    log_path = workspace / "logs" / f"{slot[:10]}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(f"{datetime.now(_KST).isoformat()} cycle {category}-{slot} OK articles={len(articles)}\n")


def main(slot: str | None = None) -> None:
    if slot is None:
        slot = datetime.now(_KST).strftime("%Y-%m-%d-%H")
    workspace = ensure_workspace()

    try:
        _classify_pending()
    except Exception as exc:
        logger.warning("classify_pending failed: %s", exc)

    failures: list[str] = []
    for category in CATEGORIES:
        try:
            _run_for_category(slot, category, workspace)
        except Exception as exc:
            logger.error("[%s] cycle failed: %s", category, exc)
            failures.append(category)
    if failures:
        raise RuntimeError(f"Cycle failed for: {', '.join(failures)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
