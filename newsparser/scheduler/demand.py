"""Build a conversation *demand* digest for the reflect/weekly jobs.

reflect/weekly run news-tainted (they read cycle reports) under
``TAINTED_FILE_TOOLS`` with no MCP access, so they cannot query the conversation
DB directly. Instead this module — plain Python, no claude — reads the store and
writes a small markdown file the job's spec then Reads (Read is allowlisted).

The digest is the *demand* signal: what the user actually asked about, to
complement the *supply* signal (what the news cycles covered).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsparser.store import conversations as conv

DIGEST_FILE = "interest-demand.md"
_MAX_QUERIES = 40
_MAX_THEMES = 20

# The reflect/weekly jobs pass a KST calendar date; events are stored as UTC
# isoformat. Interpreting the window boundary in KST and converting to UTC keeps
# events from the 00:00–08:59 KST edge (stored under the previous UTC date) inside
# the window instead of dropping up to 9 hours of them.
_KST = timezone(timedelta(hours=9))


def _cutoff(date: str, days: int) -> str:
    """A full UTC-isoformat lower bound `days` before midnight KST of `date`.

    Compared lexicographically against the store's UTC isoformat timestamps —
    both carry a ``+00:00`` offset, so string order matches chronological order."""
    kst_midnight = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=_KST) - timedelta(days=days)
    return kst_midnight.astimezone(timezone.utc).isoformat()


def build_demand_digest(date: str, days: int, max_queries: int = _MAX_QUERIES) -> str:
    since = _cutoff(date, days)
    themes = conv.interest_theme_counts(since=since)
    queries = conv.recent_user_queries(since=since, limit=max_queries)

    since_kst_date = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    lines = [
        f"# 사용자 관심 수요 (최근 {days}일, {since_kst_date} 이후)",
        "",
        "사용자가 이 기간에 실제로 물어본 내용에서 뽑은 수요 신호다. cycle 리포트(공급 신호)와",
        "별개로, 여기서 반복해 등장하는 주제는 사용자 관심 가중치를 올릴 근거가 된다.",
        "",
    ]
    if themes:
        lines.append("## 질의 빈도 상위 테마")
        for theme, count in themes[:_MAX_THEMES]:
            lines.append(f"- {theme} ({count}회)")
        lines.append("")
    if queries:
        lines.append("## 최근 질문 (최신순)")
        for q in queries:
            ts = (q.get("ts") or "")[:10]
            lines.append(f"- [{ts}] {q['content']}")
        lines.append("")
    if not themes and not queries:
        lines.append("수집된 대화 수요 신호 없음.")
    return "\n".join(lines)


def write_demand_digest(workspace: Path, date: str, days: int) -> Path:
    """Write the digest to ``<workspace>/me/interest-demand.md`` and return the path."""
    path = Path(workspace) / "me" / DIGEST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_demand_digest(date, days), encoding="utf-8")
    return path
