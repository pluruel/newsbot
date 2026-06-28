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

# Digest section headers in the report (cycle.md "Report file format").
_SCORED_SECTIONS = ("새 소식", "이어지는 흐름")        # items carry a 중요도 score
_UNSCORED_SECTIONS = ("조용한 영역", "오픈 스레드")     # items have no score
_ALL_SECTIONS = _SCORED_SECTIONS + _UNSCORED_SECTIONS
# Timestamp header line: "사이클 2026-05-08 12:00 KST".
_TIMESTAMP_RE = re.compile(r"^\s*사이클\s+\d{4}-\d{2}-\d{2}.*KST\s*$")
# Indented "엔티티: … / 출처: …" line that follows a scored item.
_META_RE = re.compile(r"^\s*(엔티티|출처)\s*[:：]")
# Any bullet item (used for the score-less quiet/open-thread sections).
_BULLET_RE = re.compile(r"^\s*[•\-\*]\s*(.+)$")

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
    """Build the Telegram lines from a saved cycle report, preserving section
    structure so context survives into the message.

    From the digest (everything before `## Graph updates`) we keep:
      - the `사이클 … KST` timestamp header,
      - the four section headers (새 소식 / 이어지는 흐름 / 조용한 영역 / 오픈 스레드),
      - for scored sections: `• 0.NN 헤드라인` (headline only, no body) plus the
        following `엔티티: … / 출처: …` line, sorted by importance descending,
      - for score-less sections: the bullet text as-is.

    Ignored entities/storylines are dropped. Duplicate scored headlines collapse
    to their HIGHEST score, keeping the section of that highest instance. Empty
    sections (and `• 없음` placeholders) are omitted. Returns [] when no item
    renders, so the caller can fall back to "새 소식 없음".
    """
    digest = report_text.split("## Graph updates", 1)[0]

    header_line: str | None = None
    # headline -> {"score", "headline", "meta": [..], "section"} (global dedup)
    scored: dict[str, dict] = {}
    # section -> list of bullet texts (quiet / open threads)
    unscored: dict[str, list[str]] = {s: [] for s in _UNSCORED_SECTIONS}

    section: str | None = None
    pending: dict | None = None  # last scored item, to attach its meta line(s)

    for raw in digest.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if header_line is None and _TIMESTAMP_RE.match(line):
            header_line = stripped
            continue
        if stripped in _ALL_SECTIONS:
            section = stripped
            pending = None
            continue
        if section is None:
            continue

        if section in _SCORED_SECTIONS:
            m = _CYCLE_ITEM_RE.match(line)
            if m:
                pending = None
                headline = _headline_only(m.group(2))
                if not headline or ignore.matches(headline):
                    continue
                score = float(m.group(1))
                existing = scored.get(headline)
                if existing is not None:
                    if score > existing["score"]:
                        existing.update(score=score, section=section, meta=[])
                        pending = existing
                    continue
                item = {"score": score, "headline": headline, "meta": [], "section": section}
                scored[headline] = item
                pending = item
                continue
            if pending is not None and _META_RE.match(line):
                pending["meta"].append(stripped)
                continue
            pending = None
        else:  # score-less section
            m = _BULLET_RE.match(line)
            if not m:
                continue
            text = m.group(1).strip()
            if not text or text == "없음" or ignore.matches(text):
                continue
            if text not in unscored[section]:
                unscored[section].append(text)

    body: list[str] = []
    for name in _ALL_SECTIONS:
        if name in _SCORED_SECTIONS:
            items = sorted((it for it in scored.values() if it["section"] == name),
                           key=lambda it: it["score"], reverse=True)
            rendered: list[str] = []
            for it in items:
                rendered.append(f"• {it['score']:.2f} {it['headline']}")
                rendered.extend(f"  {meta}" for meta in it["meta"])
        else:
            rendered = [f"• {text}" for text in unscored[name]]
        if not rendered:
            continue
        if body:
            body.append("")
        body.append(name)
        body.extend(rendered)

    if not body:
        return []
    out: list[str] = []
    if header_line:
        out += [header_line, ""]
    out += body
    return out


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
