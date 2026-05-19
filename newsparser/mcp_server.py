import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from datetime import date as _date, datetime as _datetime, time as _time, timezone as _tz
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from newsparser.graph.traversal import get_context, get_influence_chain, format_context_for_claude
from newsparser.market import store as _market_store
from newsparser.market.fetcher import TICKERS as _MARKET_TICKERS
from newsparser.store import sqlite as _sqlite_store

mcp = FastMCP("newsparser")


def _workspace() -> Path:
    return Path(os.environ.get("WORKSPACE_DIR", "workspace"))


def _log_interest_event(entity: str) -> None:
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "query",
        "entities": [entity],
        "themes": [entity],
        "depth": "shallow",
    }
    path = _workspace() / "me" / "interest-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


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
    from newsparser.bot.tracker import load_history
    history = load_history(chat_id)[-n:]
    if not history:
        return "No conversation history."
    return "\n".join(f"{t['role'].upper()}: {t['content']}" for t in history)


def _interest_weights_one(category: str, days: int) -> str:
    interests_path = _workspace() / "me" / f"interests_{category}.md"
    events_path = _workspace() / "me" / "interest-events.jsonl"

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
    if events_path.exists():
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        counts: Counter = Counter()
        for line in events_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
                for theme in e.get("themes", []):
                    counts[theme] += 1
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
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
    """Clear the interest-events.jsonl query log (resets weight estimation baseline)."""
    path = _workspace() / "me" / "interest-events.jsonl"
    if not path.exists():
        return "No interest events file found."
    path.write_text("")
    return "interest-events.jsonl cleared."


@mcp.tool()
def clear_conversation_history() -> str:
    """Clear all conversation history."""
    sessions_dir = _workspace() / "sessions"
    if not sessions_dir.exists():
        return "No sessions found."
    files = list(sessions_dir.glob("*.jsonl"))
    for f in files:
        f.write_text("")
    return f"Conversation history cleared ({len(files)} sessions)."


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
async def classify_query(query: str) -> str:
    """Return the category the query is most likely about: 'tech', 'markets', or 'both'."""
    from newsparser.classifier import classify_query as _cq
    return await _cq(query)


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


# --- Ops tools -------------------------------------------------------------

_ALLOWED_SERVICES = {"neo4j", "poller", "dispatcher"}


def _docker_ps_name(service: str) -> str | None:
    """Find a running container whose compose service label matches `service`.
    Returns the container name, or None if not found."""
    import subprocess
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", f"label=com.docker.compose.service={service}",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    name = out.splitlines()[0].strip() if out else ""
    return name or None


@mcp.tool()
def service_status() -> str:
    """List the project's docker compose services and their state.
    Use this before restarting anything or when the user asks about system health."""
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "ps", "-a",
             "--filter", "label=com.docker.compose.project",
             "--format", "{{.Names}}\t{{.Status}}\t{{.Label \"com.docker.compose.service\"}}"],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except FileNotFoundError:
        return "docker CLI not available inside this container."
    except subprocess.CalledProcessError as e:
        return f"docker ps failed: {e.stderr.strip()}"
    lines = result.stdout.strip().splitlines()
    if not lines:
        return "No compose-managed containers found."
    out = ["| service | container | status |", "|---|---|---|"]
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, status, svc = parts[0], parts[1], parts[2]
        out.append(f"| {svc} | {name} | {status} |")
    return "\n".join(out)


@mcp.tool()
def restart_service(service: str) -> str:
    """Restart one compose service (allowed: neo4j, poller, dispatcher).
    Use when the user explicitly asks to restart or reload a service.
    Restarting `dispatcher` will kill this very process — confirm with the user first."""
    import subprocess
    if service not in _ALLOWED_SERVICES:
        return f"Service '{service}' not allowed. Allowed: {sorted(_ALLOWED_SERVICES)}."
    container = _docker_ps_name(service)
    if not container:
        return f"No running container found for service '{service}'."
    try:
        subprocess.run(
            ["docker", "restart", container],
            capture_output=True, text=True, timeout=60, check=True,
        )
    except FileNotFoundError:
        return "docker CLI not available inside this container."
    except subprocess.CalledProcessError as e:
        return f"docker restart failed: {e.stderr.strip()}"
    return f"Restarted service '{service}' (container={container})."


@mcp.tool()
def tail_logs(service: str, n: int = 50) -> str:
    """Tail the last n log lines of a compose service (allowed: neo4j, poller, dispatcher).
    Use when diagnosing a problem the user reports."""
    import subprocess
    if service not in _ALLOWED_SERVICES:
        return f"Service '{service}' not allowed. Allowed: {sorted(_ALLOWED_SERVICES)}."
    container = _docker_ps_name(service)
    if not container:
        return f"No running container found for service '{service}'."
    n = max(1, min(int(n), 500))
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(n), container],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except FileNotFoundError:
        return "docker CLI not available inside this container."
    except subprocess.CalledProcessError as e:
        return f"docker logs failed: {e.stderr.strip()}"
    body = (result.stdout + result.stderr).strip()
    return body or f"(no log output for {service})"


if __name__ == "__main__":
    mcp.run(transport="stdio")
