import json
import os
from datetime import datetime, timedelta, timezone
from datetime import date as _date, datetime as _datetime, time as _time, timezone as _tz
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from newsparser.graph.traversal import get_context, get_influence_chain, format_context_for_claude
from newsparser.bots.core.jobs import KILL_FILE, REQUEST_DIR, STATE_FILE
from newsparser.classifier import classify_query as _classify_query_impl
from newsparser.market import store as _market_store
from newsparser.market.fetcher import TICKERS as _MARKET_TICKERS
from newsparser.store import sqlite as _sqlite_store
from newsparser.store import conversations as _conv

mcp = FastMCP("newsparser")


def _workspace() -> Path:
    return Path(os.environ.get("WORKSPACE_DIR", "workspace"))


def _log_interest_event(entity: str) -> None:
    _conv.log_interest_event(entity)


def _resolve_categories(category: str | None) -> list[str]:
    """Normalize a category arg into the list of underlying categories to act on.
    'both' and None mean both categories."""
    if category in (None, "both"):
        return ["tech", "markets"]
    return [category]


@mcp.tool()
def graph_query(entity: str, category: str | None = None, days: int = 7) -> str:
    """Query the knowledge graph for context about an entity.
    category='tech' or 'markets' restricts traversal to that category;
    'both' or None applies no category filter."""
    cat = None if category in (None, "both") else category
    neighbors = get_context(entity, days, category=cat)
    chains = get_influence_chain(entity, category=cat)
    _log_interest_event(entity)
    return format_context_for_claude(entity, neighbors, chains)


@mcp.tool()
def read_cycle_reports(category: str | None = None, n: int = 4) -> str:
    """Read the N most recent cycle reports.
    category='tech' or 'markets' reads only that category;
    'both' or None reads across both categories (merged by recency)."""
    cats = _resolve_categories(category)
    base = _workspace() / "cycles"
    found: list[tuple[str, Path]] = []
    for c in cats:
        d = base / c
        if d.exists():
            for f in d.glob("*.md"):
                found.append((c, f))
    if not found:
        return f"No cycle reports found for category={category or 'both'}."
    found.sort(key=lambda x: x[1].name, reverse=True)
    found = found[:n]
    found.reverse()
    return "\n\n---\n\n".join(
        f"# [{c.upper()}] {f.name}\n\n{f.read_text()}" for c, f in found
    )


@mcp.tool()
def read_conversation_history(chat_id: str, n: int = 10) -> str:
    """Read recent conversation turns for a given chat."""
    history = _conv.get_recent_messages(chat_id, n)
    if not history:
        return "No conversation history."
    return "\n".join(f"{t['role'].upper()}: {t['content']}" for t in history)


@mcp.tool()
def search_conversations(
    keyword: str, chat_id: str | None = None, since: str | None = None, n: int = 10
) -> str:
    """Full-text search past conversation turns by keyword (trigram index over all
    stored messages), newest-first. `since` is an absolute lower-bound date/datetime
    (YYYY-MM-DD). Restrict to one chat with `chat_id`. Use this to recall what was
    previously discussed with the user."""
    rows = _conv.search_messages(keyword, chat_id=chat_id, since=since, limit=n)
    if not rows:
        return f"No conversation turns matching '{keyword}'."
    out = [f"Found {len(rows)} turn(s) matching '{keyword}':\n"]
    for r in rows:
        out.append(f"[{r['ts']}] ({r['chat_id']}) {r['role'].upper()}: {r['content']}")
    return "\n".join(out)


@mcp.tool()
def get_conversation_thread(message_id: str) -> str:
    """Reconstruct the reply chain (root-first) a message belongs to, following the
    reply_to_id edges. Use this to see the exact question/answer lineage that led to
    a given turn, even when turns did not arrive strictly in order."""
    rows = _conv.get_thread(message_id)
    if not rows:
        return f"No message found with id {message_id}."
    return "\n".join(f"[{r['ts']}] {r['role'].upper()}: {r['content']}" for r in rows)


@mcp.tool()
def conversations_about_entity(entity: str, n: int = 10) -> str:
    """Find past conversation turns that mentioned a news-graph entity (by canonical
    name), newest-first — bridges the chat history and the knowledge graph. Answers
    "what have we discussed about <entity> before?"."""
    from newsparser.graph.conversation_projector import messages_about_entity
    rows = messages_about_entity(entity, n)
    if not rows:
        return f"No conversation turns mention '{entity}'."
    out = [f"{len(rows)} turn(s) mentioning '{entity}':\n"]
    for r in rows:
        out.append(f"[{r['ts']}] ({r['chat_id']}) {r['role'].upper()}: {r['content']}")
    return "\n".join(out)


def _interest_weights_one(category: str, days: int) -> str:
    interests_path = _workspace() / "me" / f"interests_{category}.md"

    actual: dict[str, dict] = {}
    if interests_path.exists():
        for line in interests_path.read_text().splitlines():
            if not line.startswith("|"):
                continue
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) < 3 or parts[0] in ("Theme", "") or set(parts[0]) <= set("-"):
                continue
            try:
                actual[parts[0]] = {
                    "interest": float(parts[1]),
                    "familiarity": float(parts[2]),
                }
            except ValueError:
                continue

    estimated: dict[str, float] = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    counts = dict(_conv.interest_theme_counts(since=cutoff))
    if counts:
        max_count = max(counts.values())
        for theme, count in counts.items():
            estimated[theme] = round(count / max_count, 2)

    if not actual and not estimated:
        return f"No data found for category={category}."

    all_themes = sorted(set(actual) | set(estimated))
    lines = [f"Interest weight comparison for category={category} (last {days} days)\n"]
    lines.append(f"{'Theme':<30} {'actual':>8} {'estimated':>10} {'diff':>6}")
    lines.append("-" * 58)
    for theme in all_themes:
        a = actual.get(theme, {}).get("interest", None)
        e = estimated.get(theme, None)
        a_str = f"{a:.2f}" if a is not None else "  —  "
        e_str = f"{e:.2f}" if e is not None else "  —  "
        diff_str = f"{(e - a):+.2f}" if (a is not None and e is not None) else "  —  "
        lines.append(f"{theme:<30} {a_str:>8} {e_str:>10} {diff_str:>6}")
    return "\n".join(lines)


@mcp.tool()
def get_interest_weights(category: str | None = None, days: int = 14) -> str:
    """Compare actual vs estimated weights for a category's interest profile.
    'both' or None returns both categories."""
    cats = _resolve_categories(category)
    return "\n\n".join(_interest_weights_one(c, days) for c in cats)


@mcp.tool()
def clear_interest_events() -> str:
    """Clear the interest-event query log (resets weight estimation baseline)."""
    removed = _conv.clear_interest_events()
    return f"interest events cleared ({removed} rows)."


@mcp.tool()
def clear_conversation_history(chat_id: str | None = None) -> str:
    """Clear stored conversation history. Omit chat_id to clear every chat."""
    removed = _conv.clear_chat(chat_id)
    scope = f"chat {chat_id}" if chat_id else "all chats"
    return f"Conversation history cleared ({removed} turns, {scope})."


@mcp.tool()
def read_interests(category: str | None = None) -> str:
    """Read the per-category interest profile.
    'both' or None returns both categories concatenated."""
    cats = _resolve_categories(category)
    parts = []
    for c in cats:
        path = _workspace() / "me" / f"interests_{c}.md"
        if path.exists():
            parts.append(f"# {c}\n\n{path.read_text()}")
        else:
            parts.append(f"# {c}\n\n(no interests file)")
    return "\n\n---\n\n".join(parts)


@mcp.tool()
def write_interests(category: str, content: str) -> str:
    """Overwrite a per-category interests file."""
    path = _workspace() / "me" / f"interests_{category}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"interests_{category}.md updated."


@mcp.tool()
def read_manifesto() -> str:
    """Read the user's manifesto (perspective/goals)."""
    path = _workspace() / "me" / "manifesto.md"
    if not path.exists():
        return "No manifesto found."
    return path.read_text()


@mcp.tool()
def write_manifesto(content: str) -> str:
    """Overwrite the user's manifesto.md."""
    path = _workspace() / "me" / "manifesto.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, ensure_ascii=False)
    return "manifesto.md updated."


@mcp.tool()
def classify_query(query: str) -> str:
    """Return the category the query is most likely about: 'tech', 'markets', or 'both'."""
    return _classify_query_impl(query)


@mcp.tool()
def market_query(
    instruments: list[str],
    start: str,
    end: str,
    freq: str = "1d",
) -> str:
    """Return OHLCV rows for the given macro instruments as compact markdown tables.

    Valid instruments: SPX, NDX, KOSPI, USDKRW, USDJPY, DXY, VIX, TNX.
    Dates must be absolute (YYYY-MM-DD). The caller is expected to resolve
    relative expressions ("최근 30일") against the current date before invoking.
    """
    _market_store.init_market_db()
    start_d = _date.fromisoformat(start)
    end_d = _date.fromisoformat(end)
    out: list[str] = []
    for alias in instruments:
        if alias not in _MARKET_TICKERS:
            out.append(f"## {alias}\n\nunknown instrument\n")
            continue
        if freq == "1d":
            rows = _market_store.get_daily(alias, start_d, end_d)
            ts_key = "date"
        elif freq == "1h":
            start_dt = _datetime.combine(start_d, _time.min, tzinfo=_tz.utc)
            end_dt = _datetime.combine(end_d, _time.max, tzinfo=_tz.utc)
            rows = _market_store.get_intraday(alias, start_dt, end_dt)
            ts_key = "ts"
        else:
            out.append(f"## {alias}\n\nunsupported freq: {freq}\n")
            continue
        if not rows:
            out.append(f"## {alias} ({freq})\n\nno data for {alias} in {start}..{end}\n")
            continue
        out.append(f"## {alias} ({freq})")
        out.append("| " + ts_key + " | open | high | low | close | volume |")
        out.append("|---|---|---|---|---|---|")
        for r in rows:
            out.append(f"| {r[ts_key]} | {r['open']} | {r['high']} | {r['low']} | {r['close']} | {r['volume']} |")
        out.append("")
    return "\n".join(out)


_ARTICLE_BODY_PREVIEW = 600


@mcp.tool()
def search_articles(keyword: str, category: str | None = None, n: int = 5) -> str:
    """Search ingested articles by keyword (case-insensitive LIKE over title and body).
    Returns up to n matches, newest first, with title/url/published/category and a
    truncated body preview. category='tech' or 'markets' restricts; None or 'both'
    searches all categories.
    Use this when the user references a specific story and wants the source article."""
    cat = None if category in (None, "both") else category
    rows = _sqlite_store.search_articles(keyword, category=cat, limit=n)
    if not rows:
        return f"No articles found matching '{keyword}'" + (
            f" in category={cat}" if cat else ""
        ) + "."
    out: list[str] = [f"Found {len(rows)} article(s) matching '{keyword}':\n"]
    for r in rows:
        body = (r.get("body") or "").strip()
        if len(body) > _ARTICLE_BODY_PREVIEW:
            body = body[:_ARTICLE_BODY_PREVIEW] + "…"
        out.append(
            f"## {r['title']}\n"
            f"- url: {r['url']}\n"
            f"- published: {r.get('published') or r['fetched_at']}\n"
            f"- source: {r['source']} | category: {r.get('category') or '?'}\n\n"
            f"{body}\n"
        )
    return "\n".join(out)


# --- Job tools ---------------------------------------------------------------
# Background jobs (cycle/weekly/reflect) run inside the dispatcher process; it
# mirrors their state to workspace/jobs.json (see bots/core/jobs.py). These tools
# read that file — they run in a separate process and cannot touch the
# dispatcher's memory.


def _fmt_elapsed(seconds: int) -> str:
    m, s = divmod(max(0, int(seconds)), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}시간 {m}분"
    if m:
        return f"{m}분 {s}초"
    return f"{s}초"


def _age_s(iso: str | None) -> int | None:
    """Seconds elapsed since an ISO timestamp, or None if unparseable."""
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return max(0, int(datetime.now(timezone.utc).timestamp() - then.timestamp()))


@mcp.tool()
def job_status() -> str:
    """현재 실행 중인 백그라운드 작업(cycle/weekly/reflect 등)과 최근 완료 이력.
    사용자가 "지금 뭐 돌아가?", "cycle 잘 되고 있어?" 같이 작업 진행 상황을 물으면 호출한다.
    running 항목의 "마지막 활동 … 전"이 수 분을 넘으면 작업이 멈춰 있을 가능성이 있다."""
    path = _workspace() / STATE_FILE
    if not path.exists():
        return f"작업 상태 파일({STATE_FILE})이 없다 — 아직 실행된 작업이 없거나 디스패처가 구버전이다."
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return f"{STATE_FILE} 읽기 실패: {e}"

    lines: list[str] = [f"상태 갱신 시각: {state.get('updated_at', '?')}"]
    running = state.get("running", [])
    if running:
        lines.append("\n실행 중:")
        for j in running:
            # elapsed/idle are recomputed from the persisted timestamps at read
            # time — the stored values are only as fresh as the last heartbeat,
            # which stops entirely when claude hangs (the case we must surface).
            elapsed = _age_s(j.get("started_at"))
            if elapsed is None:
                elapsed = j.get("elapsed_s", 0)
            line = (f"• #{j['id']} {j['bot']} ({j['trigger']}) — "
                    f"{j['started_at']} 시작, {_fmt_elapsed(elapsed)} 경과")
            act = j.get("activity")
            if act:
                idle = _age_s(act.get("last_event_at"))
                if idle is None:
                    idle = act.get("idle_s", 0)
                line += (f"\n  마지막 활동: {act['desc']} — {_fmt_elapsed(idle)} 전"
                         f" (턴 {act.get('turns', '?')}, pid {act.get('pid', '?')})")
            else:
                line += "\n  (활성 claude 서브프로세스 없음 — python 단계 실행 중일 수 있음)"
            lines.append(line)
    else:
        lines.append("\n실행 중인 작업 없음.")
    recent = state.get("recent", [])
    if recent:
        lines.append("\n최근 완료:")
        for j in recent[:5]:
            line = (f"• #{j['id']} {j['bot']} — {j['status']}, "
                    f"{j.get('finished_at', '?')} 종료, {_fmt_elapsed(j.get('elapsed_s', 0))} 소요")
            if j.get("error"):
                line += f"\n  error: {j['error'][:200]}"
            lines.append(line)
    return "\n".join(lines)


_STARTABLE_BOTS = {"cycle", "weekly", "reflect", "market_daily"}


@mcp.tool()
def start_job(bot: str, chat_id: str | None = None) -> str:
    """백그라운드 작업을 시작한다 (허용: cycle, weekly, reflect, market_daily).
    사용자가 "사이클 돌려줘", "위클리 실행해" 같이 작업 실행을 지시하면 호출한다.
    chat_id를 넘기면 완료 메시지가 그 채팅으로 간다 (프롬프트에 있는 현재 chat_id를 넘겨라).
    요청은 파일 큐로 전달되어 디스패처가 수 초 내에 집어간다."""
    if bot not in _STARTABLE_BOTS:
        return f"'{bot}'은 시작할 수 없는 작업이다. 허용: {sorted(_STARTABLE_BOTS)}."
    ws = _workspace()
    try:
        state = json.loads((ws / STATE_FILE).read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    running = next((j for j in state.get("running", []) if j.get("bot") == bot), None)
    if running is not None:
        return (f"{bot}은 이미 실행 중이다 (#{running['id']}, "
                f"{running.get('started_at', '?')} 시작). job_status()로 확인해라.")

    import uuid
    req_dir = ws / REQUEST_DIR
    req_dir.mkdir(parents=True, exist_ok=True)
    req = {"bot": bot, "chat_id": chat_id,
           "requested_at": datetime.now(timezone.utc).isoformat()}
    tmp = req_dir / f".{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(req, ensure_ascii=False))
    tmp.rename(req_dir / f"{uuid.uuid4().hex}.json")
    return (f"{bot} 시작 요청 접수 — 디스패처가 수 초 내에 실행한다. "
            "진행 상황은 job_status()로 확인할 수 있다.")


@mcp.tool()
def kill_job(job_id: int) -> str:
    """실행 중인 백그라운드 작업을 강제 중단한다. job_status()로 id를 확인하고,
    사용자에게 확인을 받은 뒤에만 호출한다."""
    ws = _workspace()
    try:
        state = json.loads((ws / STATE_FILE).read_text())
    except (OSError, json.JSONDecodeError) as e:
        return f"{STATE_FILE} 읽기 실패: {e}"
    job = next((j for j in state.get("running", []) if j.get("id") == job_id), None)
    if job is None:
        return f"실행 중인 작업 중 id={job_id}가 없다. job_status()로 확인해라."

    # This process only records the request; the dispatcher's poll picks it up
    # and kills the job's claude subprocesses in-process (killing a pid from
    # here would risk a stale/reused pid or a different container's namespace).
    kill_path = ws / KILL_FILE
    try:
        ids = json.loads(kill_path.read_text())
    except (OSError, json.JSONDecodeError):
        ids = []
    if job_id not in ids:
        ids.append(job_id)
    kill_path.write_text(json.dumps(ids))
    return (f"#{job_id} {job['bot']} 중단 요청 접수 — 디스패처가 수 초 내에 작업을 종료하고 "
            "🛑 메시지로 확인해줄 것이다.")


# --- Ops tools -------------------------------------------------------------
# All privileged operations go through /usr/local/sbin/newsbot-ops — a
# root-owned script installed OUTSIDE the repo by deploy/install.sh and
# whitelisted in sudoers with NOPASSWD. That script (not this code) validates
# actions/services, so a compromised or injected claude run editing this repo
# cannot escalate: changing the repo copy does nothing until a human re-runs
# install.sh with sudo.

_ALLOWED_SERVICES = {"neo4j", "poller", "dispatcher"}
_OPS_BIN = os.environ.get("NEWSBOT_OPS_BIN", "/usr/local/sbin/newsbot-ops")


def _run_ops(args: list[str], timeout: int = 30) -> tuple[str | None, str | None]:
    """Run `sudo -n newsbot-ops <args>`. Returns (stdout, error) — exactly one is set."""
    import subprocess
    cmd = ["sudo", "-n", _OPS_BIN, *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, "sudo 또는 newsbot-ops가 없다 — 이 호스트에서 deploy/install.sh를 먼저 실행해야 한다."
    except subprocess.SubprocessError as e:
        return None, f"newsbot-ops 실행 실패: {e}"
    if result.returncode != 0:
        err = (result.stderr or result.stdout).strip()
        return None, f"newsbot-ops 실패 (exit {result.returncode}): {err[:500]}"
    return result.stdout.strip(), None


@mcp.tool()
def service_status() -> str:
    """List the project's services (poller/dispatcher systemd units + neo4j container)
    and their state. Use this before restarting anything or when the user asks about
    system health."""
    out, err = _run_ops(["status"])
    if err:
        return err
    return out or "(no status output)"


@mcp.tool()
def restart_service(service: str) -> str:
    """Restart one service (allowed: neo4j, poller, dispatcher).
    Use when the user explicitly asks to restart or reload a service.
    `dispatcher` restarts are detached with a ~5s delay (an import check runs first),
    so the current answer still gets delivered — but any running background job's
    claude subprocess dies with it. Confirm with the user first."""
    if service not in _ALLOWED_SERVICES:
        return f"Service '{service}' not allowed. Allowed: {sorted(_ALLOWED_SERVICES)}."
    out, err = _run_ops(["restart", service], timeout=90)
    if err:
        return err
    return out or f"Restarted service '{service}'."


@mcp.tool()
def tail_logs(service: str, n: int = 50) -> str:
    """Tail the last n log lines of a service (allowed: neo4j, poller, dispatcher).
    Use when diagnosing a problem the user reports."""
    if service not in _ALLOWED_SERVICES:
        return f"Service '{service}' not allowed. Allowed: {sorted(_ALLOWED_SERVICES)}."
    n = max(1, min(int(n), 500))
    out, err = _run_ops(["logs", service, str(n)])
    if err:
        return err
    return out or f"(no log output for {service})"


if __name__ == "__main__":
    mcp.run(transport="stdio")
