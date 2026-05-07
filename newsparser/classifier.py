"""Haiku-backed classification for articles and tracker queries."""
import logging

from newsparser.claude.runner import run_claude, ClaudeError

logger = logging.getLogger(__name__)

CATEGORIES: tuple[str, str] = ("tech", "markets")

# Resolve at implementation time. We pin to the same haiku snapshot tracker.py
# uses so classifier behavior matches the rest of the system.
HAIKU_MODEL = "claude-haiku-4-5-20251001"

_BODY_EXCERPT_CHARS = 500

_ARTICLE_PROMPT = (
    "다음 기사가 어느 카테고리에 가까운지 한 단어로 답해.\n"
    "- `tech`: AI 활용·신규 AI 정보·일반 컴퓨터 기술\n"
    "- `markets`: 시장·매크로·정책·지정학·기타 산업. 애매하면 무조건 `markets`.\n\n"
    "응답은 정확히 'tech' 또는 'markets' 한 단어. 다른 설명·기호·문장부호 금지.\n\n"
    "제목: {title}\n"
    "본문 (앞 {n}자): {body}"
)

_QUERY_PROMPT = (
    "다음 사용자 쿼리가 어느 카테고리에 가까운지 한 단어로 답해.\n"
    "- `tech`: AI 활용·신규 AI 정보·일반 컴퓨터 기술 관련 질문\n"
    "- `markets`: 시장·매크로·정책·기타 산업 관련 질문\n"
    "- `both`: 두 카테고리를 모두 가로지르는 질문 (예: AI가 시장에 미치는 영향)\n\n"
    "응답은 정확히 'tech', 'markets', 또는 'both' 한 단어.\n\n"
    "쿼리: {query}"
)


def _normalize_article_response(raw: str) -> str:
    cleaned = (raw or "").strip().strip(".`'\" \t\n").lower()
    if cleaned == "tech":
        return "tech"
    if cleaned == "markets":
        return "markets"
    return "markets"  # fallback per the global tie-breaker rule


def _normalize_query_response(raw: str) -> str:
    cleaned = (raw or "").strip().strip(".`'\" \t\n").lower()
    if cleaned in ("tech", "markets", "both"):
        return cleaned
    return "both"


def classify_article(title: str, body: str | None) -> str:
    """Return 'tech' or 'markets' for a single article. Falls back to 'markets' on errors."""
    body_excerpt = (body or "")[:_BODY_EXCERPT_CHARS]
    prompt = _ARTICLE_PROMPT.format(title=title, n=_BODY_EXCERPT_CHARS, body=body_excerpt)
    try:
        raw = run_claude(prompt, timeout=15, model=HAIKU_MODEL)
    except (ClaudeError, RuntimeError, OSError) as exc:
        logger.warning("classify_article failed (%s); defaulting to 'markets'", exc)
        return "markets"
    return _normalize_article_response(raw)


def classify_query(query: str) -> str:
    """Return 'tech' / 'markets' / 'both' for a tracker query. Falls back to 'both' on errors."""
    prompt = _QUERY_PROMPT.format(query=query)
    try:
        raw = run_claude(prompt, timeout=15, model=HAIKU_MODEL)
    except (ClaudeError, RuntimeError, OSError) as exc:
        logger.warning("classify_query failed (%s); defaulting to 'both'", exc)
        return "both"
    return _normalize_query_response(raw)
