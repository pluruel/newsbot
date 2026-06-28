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

# Tokens that legitimately end with a period and must NOT be treated as the
# headline/body sentence boundary when splitting on ". ".
_ABBREV = {"inc.", "corp.", "co.", "ltd.", "vs.", "etc.", "e.g.", "i.e.", "no."}
# An initialism like "u.s." / "a.i." or a number+period like "2026." / "6.".
_NON_BOUNDARY_RE = re.compile(r"(?:[a-z]\.)+|\d+\.")


def _headline_only(text: str) -> str:
    """Return just the headline portion of a digest item — the text before the
    first sentence-ending ``. `` — without truncating inside abbreviations
    ("U.S.", "Apple Inc.") or Korean-style numeric dates ("2026. 6. 28.")."""
    i = 0
    while True:
        idx = text.find(". ", i)
        if idx == -1:
            return text.rstrip(". ").strip()
        token = text[:idx + 1].rsplit(" ", 1)[-1].lower()
        if token in _ABBREV or _NON_BOUNDARY_RE.fullmatch(token):
            i = idx + 2  # this ". " is inside an abbreviation; keep scanning
            continue
        return text[:idx].rstrip(". ").strip()


def _render_telegram(report_text: str, ignore) -> list[str]:
    """Build the terse Telegram lines from a saved cycle report.

    Extract `• (중요도 0.NN) 헤드라인. 본문…` items from the digest (everything
    before `## Graph updates`), keep only the headline, drop ignored ones, and
    return `• 0.NN 헤드라인` sorted by importance descending. Duplicate headlines
    collapse to their HIGHEST score. Items without a 중요도 score (조용한 영역 /
    오픈 스레드) are naturally excluded.
    """
    digest = report_text.split("## Graph updates", 1)[0]
    best: dict[str, float] = {}
    for line in digest.splitlines():
        m = _CYCLE_ITEM_RE.match(line)
        if not m:
            continue
        headline = _headline_only(m.group(2))
        if not headline or ignore.matches(headline):
            continue
        score = float(m.group(1))
        if score > best.get(headline, -1.0):
            best[headline] = score
    items = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    return [f"• {score:.2f} {headline}" for headline, score in items]


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
        report_text = report_path.read_text(encoding="utf-8")
        ignore = load_ignore(workspace)
        lines = _render_telegram(report_text, ignore)
        # If the digest clearly has scored items but none rendered, the report
        # likely drifted from the expected `• (중요도 0.NN)` format — surface it
        # instead of silently sending an empty "새 소식 없음".
        if not lines and "중요도" in report_text.split("## Graph updates", 1)[0]:
            logger.warning("[%s] %s: report has 중요도 items but render produced 0 lines "
                           "— possible format drift", category, slot)
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
