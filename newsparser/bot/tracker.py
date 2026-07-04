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

# Answers containing one of these markers are workspace edits (interests,
# manifesto, ignore list, history clears), not conversation — they must not be
# persisted into the chat history.
_ADMIN_MARKERS = (
    "interests_tech.md updated",
    "interests_markets.md updated",
    "manifesto.md updated",
    "ignore.md updated",
    "cleared",
    "interest-events.jsonl",
)


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
            model="claude-haiku-4-5",
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
                f"사용자: {u['content']}\n어시스턴트: {a['content']}" for u, a in recent
            )
            prev_context = f"\n\n이전 대화:\n{sections}\n"

    try:
        category_hint = classify_query(query, history=history[-5:] if history else None)
    except Exception:
        category_hint = "both"

    prompt = (
        f"질의 카테고리 힌트: {category_hint}. "
        "graph/cycle/interests 도구를 호출할 때 기본 필터로 쓰되, "
        "질문이 실제로 양쪽에 걸치면 category=None 또는 'both'로 넘긴다.\n\n"
        "너는 시장 인텔리전스 어시스턴트다. "
        "답변 전에 항상 read_cycle_reports()를 먼저 호출해 최근 사이클 맥락을 로드한다. "
        "그다음 필요에 따라 graph_query 등 다른 도구를 쓴다. "
        "사이클 리포트는 날짜로 인용한다. 답이 길면 TL;DR로 시작한다.\n\n"
        "근거 규칙:\n"
        "- 모든 사실 주장(사건, 수치, 날짜, 발언, 인과관계)은 이 대화에서 도구로 직접 확인한 "
        "자료(사이클 리포트, search_articles 원문, graph_query, market_query)에만 근거한다. "
        "일반 지식이나 기억으로 기사 내용·수치를 채우지 않는다.\n"
        "- 각 주장 뒤에 출처를 명시한다: 사이클 리포트는 날짜·슬롯, 기사는 제목(가능하면 URL), "
        "시세는 market_query 기준일. 출처를 댈 수 없는 내용은 답변에 넣지 않는다.\n"
        "- 도구로 찾아도 자료가 없으면 추측으로 메우지 말고 \"수집된 자료에 없다\"고 명확히 말한다. "
        "그 위에 추론을 덧붙일 때는 반드시 추측임을 표시하고 근거 사실과 구분한다.\n\n"
        "사용자가 특정 기사 원문/내용을 묻거나(\"그 기사 보여줘\", \"H200 중국 승인 기사\"), "
        "사이클 요약에 없는 디테일을 요구하면 `search_articles(keyword, category, n)`로 "
        "원문(title/url/body)을 직접 조회한다.\n\n"
        "시계열·가격·환율 질문이 들어오면 `market_query` 도구를 쓴다. "
        "`start`/`end`는 항상 절대 날짜(YYYY-MM-DD). "
        "사용자가 \"최근 한 달\" 같이 말하면 오늘 날짜 기준으로 직접 변환해서 넣는다. "
        "유효 instruments: SPX, NDX, KOSPI, USDKRW, USDJPY, DXY, VIX, TNX.\n\n"
        "백그라운드 작업(cycle/weekly/reflect) 상태를 물으면(\"지금 뭐 돌아가?\", "
        "\"cycle 잘 되고 있어?\", \"작업 진행 상황 보고해\") `job_status` 도구를 호출해 "
        "실행 중 작업·경과 시간·마지막 활동을 확인해 답한다. 마지막 활동(idle_s)이 "
        "수 분 이상이면 정체 가능성을 언급한다. 사용자가 작업 중단을 지시하면 "
        "`kill_job(job_id)`를 사용자 확인 후에만 호출한다.\n\n"
        "운영(ops) 권한도 있다. 사용자가 봇/서비스 상태나 재시작·로그를 묻거나 "
        "지시하면 `service_status`, `tail_logs(service, n)`, `restart_service(service)` "
        "MCP 도구를 쓴다. 허용 서비스: neo4j, poller, dispatcher. "
        "`restart_service('dispatcher')`는 현재 프로세스를 죽이므로 반드시 사용자 확인 후에만 호출한다. "
        "이 외에 호스트 환경을 만질 필요가 있으면 Bash 도구로 직접 명령을 실행할 수 있다 "
        "(docker, ls, cat, .venv/bin/python 등). 단, 파괴적 명령(rm -rf, drop, force push)은 "
        "사용자 확인을 받는다.\n\n"
        "무시 목록 관리 권한도 있다. 사용자가 특정 엔티티/서사를 더는 다루지 말라고 하면"
        "(\"무시: X\", \"X 무시해\"), `workspace/me/ignore.md` 표에 행을 추가한다. "
        "단일 엔티티명이면 종류=entity, 서사·주장 문구면 종류=storyline, 추가일은 오늘(YYYY-MM-DD). "
        "\"무시 해제: X\"면 해당 행을 삭제한다. 이렇게 ignore.md를 편집한 경우 답변에 "
        "정확히 `ignore.md updated` 문구를 포함한다. "
        "\"차단 리스트\"/\"무시 목록 보여줘\"면 `.venv/bin/python -m newsparser.ignore`를 "
        "Bash로 실행해 그 출력(대상 + N일 경과)을 그대로 사용자에게 전달한다.\n\n"
        "답변은 평문 대화체 문단으로만 쓴다 — 마크다운 금지: 헤더(#), "
        "볼드(**), 불릿(-/*), 표, 수평선(---) 모두 쓰지 않는다. "
        "섹션 구분은 빈 줄로만 한다."
        f"{prev_context}\n\n"
        f"사용자 질문: {query}"
    )

    answer = run_claude(
        prompt,
        mcp_config=str(_MCP_CONFIG),
        allowed_tools=["Bash", "Read", "Edit", "Write", "Grep", "Glob"],
        permission_mode="bypassPermissions",
    )

    if not any(marker in answer for marker in _ADMIN_MARKERS):
        now = datetime.now(timezone.utc).isoformat()
        new_turns = history + [
            {"role": "user", "content": query, "ts": now},
            {"role": "assistant", "content": answer, "ts": now},
        ]
        save_history(chat_id, new_turns)
    return answer
