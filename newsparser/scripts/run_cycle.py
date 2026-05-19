# newsparser/scripts/run_cycle.py
import asyncio
import logging
import sys
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


async def _classify_pending() -> None:
    rows = get_unclassified()
    if not rows:
        return
    logger.info("Classifying %d untagged articles", len(rows))
    for r in rows:
        try:
            cat = await classify_article(r["title"], r["body"])
        except Exception as exc:
            logger.warning("Classifier error on %s: %s — defaulting to markets", r["guid"], exc)
            cat = "markets"
        update_category(r["guid"], cat)


async def _run_for_category(slot: str, category: str, workspace: Path) -> None:
    articles = get_unprocessed(category=category)
    if not articles:
        logger.info("No unprocessed articles for category=%s slot=%s", category, slot)
        return

    guids_path = workspace / "input" / category / f"{slot}-guids.txt"
    guids_path.parent.mkdir(parents=True, exist_ok=True)
    guids_path.write_text("\n".join(a["guid"] for a in articles))

    build_input_file(slot, category)
    logger.info("[%s] Built input file (%d articles)", category, len(articles))

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

    await run_claude(f"/cycle {slot} {category}")
    logger.info("[%s] Claude cycle complete", category)

    if guids_path.exists():
        logger.warning("[%s] guids file still present after run_claude — marking processed directly", category)
        guids = [g for g in guids_path.read_text().splitlines() if g.strip()]
        if guids:
            mark_processed(guids)
        guids_path.unlink()

    report_path = workspace / "cycles" / category / f"{slot}.md"
    if report_path.exists():
        report = report_path.read_text(encoding="utf-8")
        digest = report.split("## Graph updates", 1)[0].rstrip()
        message = f"[{category.upper()}] {digest}" if digest else f"[{category.upper()}] (empty digest)"
        try:
            await asyncio.to_thread(send_long_message, message)
        except Exception as e:
            logger.error("Telegram send failed for %s/%s: %s", category, slot, e)

    log_path = workspace / "logs" / f"{slot[:10]}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(f"{datetime.now(_KST).isoformat()} cycle {category}-{slot} OK articles={len(articles)}\n")


async def main(slot: str | None = None) -> None:
    if slot is None:
        slot = datetime.now(_KST).strftime("%Y-%m-%d-%H")
    workspace = ensure_workspace()

    try:
        await _classify_pending()
    except Exception as exc:
        logger.warning("classify_pending failed: %s", exc)

    for category in CATEGORIES:
        try:
            await _run_for_category(slot, category, workspace)
        except Exception as exc:
            logger.error("[%s] cycle failed: %s", category, exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
