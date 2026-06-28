# newsparser/scripts/run_cycle.py
import logging
import os
import re
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
from newsparser.ignore import load_ignore

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

_CYCLE_ITEM_RE = re.compile(r"^\s*[•\-\*]\s*\(중요도\s*([0-9]*\.?[0-9]+)\)\s*(.+)$")


def _render_telegram(report_text: str, ignore) -> list[str]:
    """Build the terse Telegram lines from a saved cycle report.

    Extract `• (중요도 0.NN) 헤드라인. 본문…` items from the digest (everything
    before `## Graph updates`), keep only the headline (text before the first
    sentence end), drop ignored ones, and return `• 0.NN 헤드라인` sorted by
    importance descending. Items without a 중요도 score (조용한 영역 / 오픈 스레드)
    are naturally excluded.
    """
    digest = report_text.split("## Graph updates", 1)[0]
    items: list[tuple[float, str]] = []
    seen: set[str] = set()
    for line in digest.splitlines():
        m = _CYCLE_ITEM_RE.match(line)
        if not m:
            continue
        score = float(m.group(1))
        headline = m.group(2).split(". ", 1)[0].rstrip(". ").strip()
        if not headline or headline in seen:
            continue
        if ignore.matches(headline):
            continue
        seen.add(headline)
        items.append((score, headline))
    items.sort(key=lambda x: x[0], reverse=True)
    return [f"• {score:.2f} {headline}" for score, headline in items]


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
    guids_path.write_text("\n".join(a["guid"] for a in articles))

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

    run_claude(f"/cycle {slot} {category}")
    logger.info("[%s] Claude cycle complete", category)

    # Safety net: if the slash command's mark_processed.py call was skipped or failed,
    # the guids file still exists. Mark them here to prevent reprocessing on next cycle.
    if guids_path.exists():
        logger.warning("[%s] guids file still present after run_claude — marking processed directly", category)
        guids = [g for g in guids_path.read_text().splitlines() if g.strip()]
        if guids:
            mark_processed(guids)
        guids_path.unlink()

    # Telegram gets a terse, importance-sorted list rendered deterministically
    # from the saved report file (NOT the LLM stdout), with ignored
    # entities/storylines dropped. The full digest stays in the report file.
    report_path = workspace / "cycles" / category / f"{slot}.md"
    if report_path.exists():
        ignore = load_ignore(workspace)
        lines = _render_telegram(report_path.read_text(encoding="utf-8"), ignore)
        body = "\n".join(lines) if lines else "새 소식 없음"
        try:
            send_long_message(f"[{category.upper()}]\n{body}")
        except Exception as e:
            logger.error("Telegram send failed for %s/%s: %s", category, slot, e)
    else:
        logger.warning("[%s] no report file at %s — skipping telegram",
                       category, report_path)

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

    for category in CATEGORIES:
        try:
            _run_for_category(slot, category, workspace)
        except Exception as exc:
            logger.error("[%s] cycle failed: %s", category, exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
