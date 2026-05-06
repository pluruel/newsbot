import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsparser.claude.runner import run_claude
from newsparser.scheduler.workspace import ensure_workspace

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 14


def interests_rollup() -> None:
    """Analyze recent tracker events and update interests.md via Claude."""
    workspace = ensure_workspace()
    events_path = workspace / "me" / "interest-events.jsonl"
    interests_path = workspace / "me" / "interests.md"

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    events: list[dict] = []
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
                if ts >= cutoff:
                    events.append(e)
            except Exception:
                continue

    if not events:
        logger.info("No interest events in last %d days — skipping rollup", LOOKBACK_DAYS)
        return

    current_interests = interests_path.read_text(encoding="utf-8") if interests_path.exists() else ""

    events_block = "\n".join(
        f"- [{e['ts']}] query: {e['themes'][0] if e.get('themes') else ''} | entities: {', '.join(e.get('entities', []))}"
        for e in events
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = (
        f"아래는 사용자의 최근 {LOOKBACK_DAYS}일간 tracker 쿼리 이벤트야.\n"
        "각 이벤트에는 쿼리 텍스트와 실제 graph에서 히트된 엔티티가 포함되어 있어.\n"
        "현재 interests.md도 같이 줄게.\n\n"
        f"## 쿼리 이벤트\n{events_block}\n\n"
        f"## 현재 interests.md\n{current_interests}\n\n"
        "위 데이터를 분석해서 새 interests.md를 작성해줘. 규칙:\n"
        "- 반복 등장하는 엔티티나 테마를 관심사로 추론해\n"
        "- '안녕', '기사 보여줘', '요약해줘' 같은 메타 쿼리는 무시해\n"
        "- 기존 ## User overrides 내용은 반드시 그대로 보존하면서 병합해\n"
        "- ## Themes 섹션을 업데이트해\n"
        f"- Last updated를 {today}로 갱신해\n"
        "- 파일 전체 내용을 raw markdown으로만 출력해. 설명이나 코드블록 없이."
    )

    updated = run_claude(prompt)
    interests_path.write_text(updated, encoding="utf-8")
    logger.info("interests.md updated via rollup")
