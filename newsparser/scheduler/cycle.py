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
from newsparser.classifier import classify_article, CATEGORIES
from newsparser.graph.writer import apply_graph_updates
from newsparser.store.sqlite import (
    get_unclassified, get_unprocessed, init_db, mark_processed, update_category,
)
from newsparser.scheduler.lock import acquire_lock, release_lock, LockError
from newsparser.scheduler.workspace import ensure_workspace

logger = logging.getLogger(__name__)

_CYCLE_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "cycle.md"

_SCOPE_TEXT = {
    "tech": (
        "AI 활용·신규 AI 정보·일반 컴퓨터 기술. "
        "시장 영향 일반 산업 뉴스는 markets 사이클에서 처리하므로 다루지 마."
    ),
    "markets": (
        "시장·매크로·정책·지정학·일반 산업. "
        "AI 회사 실적·주가 영향처럼 시장 관점이면 여기서 다뤄도 됨."
    ),
}


def _classify_pending() -> int:
    """Tag any unprocessed articles with NULL category via haiku. Returns count tagged."""
    rows = get_unclassified()
    if not rows:
        return 0
    logger.info("Classifying %d untagged articles via haiku", len(rows))
    for r in rows:
        try:
            cat = classify_article(r["title"], r["body"])
        except Exception as exc:  # defense in depth — classifier already catches its own errors
            logger.warning("Unexpected classifier error on %s: %s — defaulting to 'markets'", r["guid"], exc)
            cat = "markets"
        update_category(r["guid"], cat)
    return len(rows)


def _interests_text(workspace: Path, category: str) -> str:
    path = workspace / "me" / f"interests_{category}.md"
    if not path.exists():
        return "(no interests file yet)"
    return path.read_text(encoding="utf-8")


def _build_prompt(spec: str, category: str, workspace: Path, input_path: Path) -> str:
    header = (
        "## 카테고리\n"
        f"현재 사이클: {category}\n"
        f"범위: {_SCOPE_TEXT[category]}\n\n"
        "## 사용자 관심사\n"
        f"{_interests_text(workspace, category)}\n"
    )
    return f"{header}\n{spec}\n\nInput file: {input_path}"


def _run_for_category(slot: str, category: str, workspace: Path) -> None:
    unprocessed = get_unprocessed(category=category)
    if not unprocessed:
        logger.info("No unprocessed articles for category=%s slot=%s", category, slot)
        return

    input_path = build_input_file(slot, category)
    logger.info("[%s] Built input file: %s (%d articles)", category, input_path, len(unprocessed))

    spec = _CYCLE_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = _build_prompt(spec, category, workspace, input_path)
    report = run_claude(prompt)

    report_path = workspace / "cycles" / category / f"{slot}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    logger.info("[%s] Cycle report written: %s", category, report_path)

    entities, relations = parse_graph_updates(report)
    cycle_id = f"{category}-{slot}"
    apply_graph_updates(entities, relations, cycle_id=cycle_id, category=category)
    logger.info("[%s] Graph updated: %d entities, %d relations", category, len(entities), len(relations))

    digest = report.split("## Graph updates", 1)[0].rstrip()
    message = f"[{category.upper()}] {digest}" if digest else f"[{category.upper()}] (empty digest)"
    try:
        send_long_message(message)
    except Exception as e:
        logger.error("Telegram send failed for cycle %s/%s: %s", category, slot, e)

    mark_processed([a["guid"] for a in unprocessed])

    log_path = workspace / "logs" / f"{slot[:10]}.log"
    with log_path.open("a") as f:
        f.write(
            f"{datetime.now(_KST).isoformat()} cycle {cycle_id} OK "
            f"articles={len(unprocessed)} entities={len(entities)} relations={len(relations)}\n"
        )


def run_cycle(slot: str) -> None:
    """Full /cycle flow per slot — classifies pending then runs once per category."""
    workspace = ensure_workspace()
    init_db()  # idempotent — ensures category column exists on pre-existing DBs
    lock_path = workspace / "state" / "lockfile"

    try:
        acquire_lock(lock_path)
    except LockError as e:
        logger.warning("Cycle aborted: %s", e)
        return

    try:
        try:
            _classify_pending()
        except Exception as exc:
            logger.warning("classify_pending failed (%s); proceeding with already-tagged rows", exc)

        for category in CATEGORIES:
            _run_for_category(slot, category, workspace)
    finally:
        release_lock(lock_path)
