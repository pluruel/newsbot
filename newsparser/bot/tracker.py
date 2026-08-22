import logging
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from newsparser.claude.haiku import ask_haiku
from newsparser.claude.runner import run_claude
from newsparser.classifier import classify_query
from newsparser.gemini import PLAIN_KOREAN_STYLE, summarize_youtube
from newsparser.store import conversations as conv

logger = logging.getLogger(__name__)

HISTORY_MAX_TURNS = 10

_KST = ZoneInfo("Asia/Seoul")

_MCP_CONFIG = Path(__file__).parent.parent.parent / "mcp.json"

TRACKER_MODEL = "claude-opus-5"

_ADMIN_MARKERS = (
    "interests_tech.md updated",
    "interests_markets.md updated",
    "manifesto.md updated",
    "ignore.md updated",
    "interest events cleared",
    "Conversation history cleared",
)


# Standing instructions. These go to --append-system-prompt, not into the user
# turn: they are identical on every call, and the per-turn prompt should carry
# only what actually changes (clock, chat_id, category hint, history, question).
#
# Rules about how to *use* a specific tool belong in that tool's docstring in
# mcp_server.py — the model receives those with the tool schema, so repeating
# them here only pays for the same text twice. What stays here is what no single
# tool owns: role, evidence policy, workspace-file conventions, output style.
SYSTEM_PROMPT = (
    "너는 시장 인텔리전스 어시스턴트다. "
    "답변 전에 항상 read_cycle_reports()를 먼저 호출해 최근 사이클 맥락을 로드하고, "
    "그다음 필요에 따라 graph_query 등 다른 도구를 쓴다. "
    "답이 길면 TL;DR로 시작한다.\n\n"
    "근거 규칙:\n"
    "- 모든 사실 주장(사건, 수치, 날짜, 발언, 인과관계)은 이 대화에서 도구로 직접 확인한 "
    "자료(사이클 리포트, search_articles 원문, graph_query, market_query)에만 근거한다. "
    "일반 지식이나 기억으로 기사 내용·수치를 채우지 않는다.\n"
    "- 각 주장 뒤에 출처를 명시한다: 사이클 리포트는 날짜·슬롯, 기사는 제목(가능하면 URL), "
    "시세는 market_query 기준일. 출처를 댈 수 없는 내용은 답변에 넣지 않는다.\n"
    "- 도구로 찾아도 자료가 없으면 추측으로 메우지 말고 \"수집된 자료에 없다\"고 명확히 말한다. "
    "그 위에 추론을 덧붙일 때는 반드시 추측임을 표시하고 근거 사실과 구분한다.\n\n"
    "유튜브 링크가 온 턴은 Gemini가 영상을 보고 요약하며, 그 요약은 대화 기록에는 남지만 "
    "지식그래프에는 들어가지 않는다 — 영상 속 주장이 검증된 기사 사실과 섞이지 않게 하려는 것이다. "
    "따라서 이미 반영돼 있다고 답하지 마라. 사용자가 나중에 그 영상을 그래프에 넣어달라고 하면"
    "(\"방금 그 영상 지식그래프에도 반영해\") `project_conversation(chat_id, n)`을 호출한다.\n\n"
    "Bash 도구로 호스트 환경을 직접 볼 수 있다(ls, cat, journalctl, .venv/bin/python 등). "
    "단, 파괴적 명령(rm -rf, drop, force push)은 사용자 확인을 받는다.\n\n"
    "무시 목록 관리 권한도 있다. 사용자가 특정 엔티티/서사를 더는 다루지 말라고 하면"
    "(\"무시: X\", \"X 무시해\"), `workspace/me/ignore.md` 표에 행을 추가한다. "
    "단일 엔티티명이면 종류=entity, 서사·주장 문구면 종류=storyline, 추가일은 오늘(YYYY-MM-DD). "
    "\"무시 해제: X\"면 해당 행을 삭제한다. "
    "\"차단 리스트\"/\"무시 목록 보여줘\"면 `.venv/bin/python -m newsparser.ignore`를 "
    "Bash로 실행해 그 출력(대상 + N일 경과)을 그대로 사용자에게 전달한다.\n\n"
    "워크스페이스 편집 도구(write_interests, write_manifesto, clear_interest_events, "
    "clear_conversation_history)나 ignore.md 편집을 수행한 경우, 그 도구가 돌려준 영어 확인 "
    "문구(interests_tech.md updated / interests_markets.md updated / manifesto.md updated / "
    "ignore.md updated / interest events cleared / Conversation history cleared)를 답변에 "
    "반드시 그 형태 그대로 한 번 포함한다 — 이 문구로 관리 작업을 식별해 대화 기록에서 제외하기 "
    "때문이다. 편집을 하지 않았다면 이 문구들을 쓰지 않는다.\n\n"
    f"{PLAIN_KOREAN_STYLE}"
)


def load_history(chat_id: str) -> list[dict]:
    """Recent conversational turns for a chat, oldest-first (admin turns excluded)."""
    return conv.get_recent_messages(chat_id, HISTORY_MAX_TURNS)


def _extract_pairs(history: list[dict]) -> list[tuple[dict, dict]]:
    """Pair each user turn with the assistant turn that answered it.

    Prefers the explicit ``reply_to_id`` edge, so pairing survives non-sequential
    arrival (e.g. two user messages before either is answered). Falls back to
    positional user→assistant alternation for turns without an edge."""
    by_id = {m["id"]: m for m in history if m.get("id")}
    pairs: list[tuple[dict, dict]] = []
    for m in history:
        if m.get("role") != "assistant":
            continue
        parent = by_id.get(m.get("reply_to_id"))
        if parent is not None and parent.get("role") == "user":
            pairs.append((parent, m))
    if pairs:
        return pairs

    # Fallback: no reply edges present — walk positionally as before.
    i = 0
    while i < len(history) - 1:
        if history[i].get("role") == "user" and history[i + 1].get("role") == "assistant":
            pairs.append((history[i], history[i + 1]))
            i += 2
        else:
            i += 1
    return pairs


_HAIKU_ASSISTANT_PREVIEW = 240
_DEPTH_MAX_TOKENS = 8


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
        result = ask_haiku(
            prompt,
            f"Reply with only a single integer between 0 and {max_depth}. No other text.",
            _DEPTH_MAX_TOKENS,
            timeout=30,
            usage_tag="tracker_depth",
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
        f"지금은 {datetime.now(_KST).strftime('%Y-%m-%d %H:%M')} KST다 "
        "(claude CLI가 도는 호스트의 시간대는 KST가 아닐 수 있으니 네 자체 시계 대신 이 값을 써라). "
        "\"오늘\", \"어제\", \"3시간 전\" 같은 상대 표현은 전부 이 시각으로 환산하고, "
        "사용자에게 답할 때 쓰는 시각·날짜도 KST로 적는다. "
        "도구가 돌려주는 타임스탬프는 소스마다 시간대가 다르니 각 도구 설명을 따른다.\n\n"
        f"현재 chat_id는 {chat_id}다 — start_job·project_conversation에 넘길 값이다.\n\n"
        f"질의 카테고리 힌트: {category_hint}. "
        "graph/cycle/interests 도구를 호출할 때 기본 필터로 쓰되, "
        "질문이 실제로 양쪽에 걸치면 category=None 또는 'both'로 넘긴다."
        f"{prev_context}\n\n"
        f"사용자 질문: {query}"
    )

    answer = run_claude(
        prompt,
        mcp_config=str(_MCP_CONFIG),
        model=TRACKER_MODEL,
        append_system_prompt=SYSTEM_PROMPT,
        allowed_tools=["Bash", "Read", "Edit", "Write", "Grep", "Glob"],
        permission_mode="bypassPermissions",
    )

    _persist_exchange(chat_id, query, answer)
    return answer


def _persist_exchange(chat_id: str, query: str, answer: str,
                      project: bool = True) -> None:
    """Record one user→assistant turn and, unless told otherwise, mirror it
    into the graph.

    Shared by every path that answers the user, so a YouTube summary lands in
    the same history a follow-up question reads back. `project=False` keeps a
    turn out of Neo4j — see run_youtube for why video content does not belong
    in the news graph.
    """
    # Admin/workspace-edit answers are recorded with kind='admin' — kept for audit
    # but excluded from the conversational context that load_history returns.
    kind = "admin" if any(marker in answer for marker in _ADMIN_MARKERS) else "chat"
    user_id = conv.add_message(chat_id, "user", query, kind=kind)
    asst_id = conv.add_message(
        chat_id, "assistant", answer, reply_to_id=user_id, kind=kind
    )

    if kind == "chat" and project:
        # Project into Neo4j off the reply path: a Neo4j outage stalls the driver
        # for its whole connection timeout, and blocking the reply on a best-effort
        # mirror is wrong (SQLite is already the source of truth). Fire-and-forget
        # on a daemon thread so the answer returns immediately.
        threading.Thread(
            target=_project_exchange_bg,
            args=(chat_id, user_id, asst_id),
            daemon=True,
        ).start()


def run_youtube(chat_id: str, query: str, url: str, instruction: str) -> str:
    """Summarise a YouTube link with Gemini and record the turn.

    Deliberately skips the tracker's MCP tools: the user asked for the video's
    own content, and mixing in cycle reports and graph context muddies it. The
    summary still lands in conversation history, so a follow-up question
    ("그 영상이랑 이번 사이클이랑 어떻게 엮여?") reaches the tracker with the
    video already in context — the graph lookup happens on that next turn.

    The Neo4j projection is skipped: projecting entity-links the summary, so a
    video's claims would sit in the knowledge graph next to vetted
    article-derived facts with nothing to tell them apart. When the user does
    want it in there they say so on a later turn, and the tracker puts it in
    with the `project_conversation` MCP tool.

    Raises GeminiError — the caller reports it rather than silently falling back
    to a Claude answer written without having watched the video.
    """
    answer = summarize_youtube(url, instruction)
    _persist_exchange(chat_id, query, answer, project=False)
    return answer


def _project_exchange_bg(chat_id: str, user_id: str, asst_id: str) -> None:
    try:
        from newsparser.graph.conversation_projector import project_exchange
        project_exchange(chat_id, user_id, asst_id)
    except Exception:
        logger.exception("conversation graph projection failed")
