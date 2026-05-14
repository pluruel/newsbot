import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from newsparser.claude.runner import run_claude
from newsparser.classifier import classify_query

logger = logging.getLogger(__name__)

HISTORY_MAX_TURNS = 10

_MCP_CONFIG = Path(__file__).parent.parent.parent / "mcp.json"


def _workspace() -> Path:
    return Path(os.environ.get("WORKSPACE_DIR", "workspace"))


def load_history(chat_id: str) -> list[dict]:
    path = _workspace() / "sessions" / f"{chat_id}.jsonl"
    if not path.exists():
        return []
    turns = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return turns[-HISTORY_MAX_TURNS:]


def save_history(chat_id: str, turns: list[dict]) -> None:
    path = _workspace() / "sessions" / f"{chat_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))


def _extract_pairs(history: list[dict]) -> list[tuple[dict, dict]]:
    """Walk history and pair each user turn with its following assistant turn."""
    pairs: list[tuple[dict, dict]] = []
    i = 0
    while i < len(history) - 1:
        if history[i].get("role") == "user" and history[i + 1].get("role") == "assistant":
            pairs.append((history[i], history[i + 1]))
            i += 2
        else:
            i += 1
    return pairs


_HAIKU_ASSISTANT_PREVIEW = 240


def _needed_history_depth(query: str, history: list[dict], max_depth: int) -> int:
    """Use haiku to decide how many recent exchanges the new query needs (0..max_depth)."""
    if max_depth <= 0:
        return 0
    lines = []
    for t in history:
        content = t["content"]
        if t.get("role") == "assistant" and len(content) > _HAIKU_ASSISTANT_PREVIEW:
            content = content[:_HAIKU_ASSISTANT_PREVIEW] + "…"
        lines.append(f"{t['role'].upper()}: {content}")
    transcript = "\n".join(lines)
    prompt = (
        "Below is a conversation history followed by a new query. "
        "How many of the most recent question-and-answer exchanges does the new query "
        "need in order to be answered correctly? Older context that is not needed should not be counted. "
        f"Reply with only a single integer between 0 and {max_depth}.\n\n"
        f"Conversation history:\n{transcript}\n\n"
        f"New query: {query}"
    )
    try:
        result = run_claude(
            prompt,
            timeout=30,
            model="claude-haiku-4-5-20251001",
            system_prompt=(
                f"Reply with only a single integer between 0 and {max_depth}. No other text."
            ),
        )
        token = result.strip().split()[0].strip(".,!?") if result.strip() else "1"
        n = int(token)
    except Exception:
        return min(1, max_depth)
    return max(0, min(n, max_depth))


def run_tracker(chat_id: str, query: str) -> str:
    """Resolve a user query using Claude with MCP tools."""
    history = load_history(chat_id)

    prev_context = ""
    pairs = _extract_pairs(history)
    if pairs:
        depth = _needed_history_depth(query, history, max_depth=len(pairs))
        if depth > 0:
            recent = pairs[-depth:]
            sections = "\n\n".join(
                f"User: {u['content']}\nAssistant: {a['content']}" for u, a in recent
            )
            prev_context = f"\n\nPrevious exchanges:\n{sections}\n"

    try:
        category_hint = classify_query(query, history=history[-5:] if history else None)
    except Exception:
        category_hint = "both"

    prompt = (
        f"User query category hint: {category_hint}. "
        "Use this as a default filter when calling graph/cycle/interests tools, "
        "but pass category=None or 'both' if the question genuinely spans both.\n\n"
        "You are a market intelligence assistant. "
        "Always call read_cycle_reports() first to load recent cycle context before answering. "
        "Then use graph_query or other tools as needed. "
        "Cite cycle reports by date. Lead with TL;DR if the answer is long.\n\n"
        "사용자가 특정 기사 원문/내용을 묻거나(\"그 기사 보여줘\", \"H200 중국 승인 기사\"), "
        "사이클 요약에 없는 디테일을 요구하면 `search_articles(keyword, category, n)`로 "
        "원문(title/url/body)을 직접 조회한다.\n\n"
        "시계열·가격·환율 질문이 들어오면 `market_query` 도구를 쓴다. "
        "`start`/`end`는 항상 절대 날짜(YYYY-MM-DD). "
        "사용자가 \"최근 한 달\" 같이 말하면 오늘 날짜 기준으로 직접 변환해서 넣는다. "
        "유효 instruments: SPX, NDX, KOSPI, USDKRW, USDJPY, DXY, VIX, TNX.\n\n"
        "운영(ops) 권한도 있다. 사용자가 봇/서비스 상태나 재시작·로그를 묻거나 "
        "지시하면 `service_status`, `tail_logs(service, n)`, `restart_service(service)` "
        "MCP 도구를 쓴다. 허용 서비스: neo4j, poller, dispatcher. "
        "`restart_service('dispatcher')`는 현재 프로세스를 죽이므로 반드시 사용자 확인 후에만 호출한다. "
        "이 외에 호스트 환경을 만질 필요가 있으면 Bash 도구로 직접 명령을 실행할 수 있다 "
        "(docker, ls, cat, .venv/bin/python 등). 단, 파괴적 명령(rm -rf, drop, force push)은 "
        "사용자 확인을 받는다.\n\n"
        "Answer in plain conversational paragraphs — no markdown: no headers (#), "
        "no bold (**), no bullet lists (-/*), no tables, no horizontal rules (---). "
        "Separate sections with blank lines only."
        f"{prev_context}\n\n"
        f"User query: {query}"
    )

    answer = run_claude(
        prompt,
        mcp_config=str(_MCP_CONFIG),
        allowed_tools=["Bash", "Read", "Edit", "Write", "Grep", "Glob"],
        permission_mode="bypassPermissions",
    )

    _ADMIN_MARKERS = (
        "interests_tech.md updated",
        "interests_markets.md updated",
        "manifesto.md updated",
        "cleared",
        "interest-events.jsonl",
    )
    if not any(marker in answer for marker in _ADMIN_MARKERS):
        now = datetime.now(timezone.utc).isoformat()
        new_turns = history + [
            {"role": "user", "content": query, "ts": now},
            {"role": "assistant", "content": answer, "ts": now},
        ]
        save_history(chat_id, new_turns)
    return answer
