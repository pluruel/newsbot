"""Project the SQLite conversation store into Neo4j (best-effort).

SQLite is the source of truth (see ``newsparser/store/conversations.py``); this
module mirrors messages into the graph so conversation flow can be traversed and,
crucially, linked to the news knowledge graph:

    (:Message {id, chat_id, role, ts, kind})
    (:Message)-[:IN_CHAT]->(:Chat {chat_id})
    (:Message)-[:REPLIES_TO]->(:Message)      -- the reply_to_id DAG edge
    (:Message)-[:MENTIONS]->(:Entity)         -- news-graph entities named in the text

Every write is best-effort: a Neo4j outage must never break a Telegram reply, so
callers wrap projection in try/except and failures are logged, not raised. Because
the graph is derived, ``reproject_all()`` can rebuild it from SQLite at any time.

One projection policy for both paths: ``_project_one`` is the single place that
decides what nodes/edges a message gets, so the live per-turn path and the full
rebuild produce identical graphs (mentions are linked on real user turns only —
the question carries the entities; the assistant turn is reachable via REPLIES_TO).
"""

import logging
import time

from newsparser.graph.neo4j_client import get_driver
from newsparser.ignore import _matches_target
from newsparser.store import conversations as conv

logger = logging.getLogger(__name__)

# A name must be at least this long to be worth a substring/word-boundary match
# (avoids linking on 1-char noise; word boundaries handle the 'AI'∈'TAIWAN' case).
_MIN_ENTITY_LEN = 2
# Cross-label entity registry is scanned once and cached — it changes slowly and
# a fresh scan per chat turn was the dominant projection cost.
_REGISTRY_LIMIT = 500
_REGISTRY_TTL_S = 300.0
_registry_cache: tuple[float, list[tuple[str, tuple[str, ...]]]] | None = None

# Uniqueness constraints turn the per-message MERGE from a label scan into an
# index lookup. Ensured once per process (idempotent, but CREATE CONSTRAINT has
# real latency, so we don't run it on every turn).
_schema_ensured = False


def _ensure_schema(session) -> None:
    global _schema_ensured
    if _schema_ensured:
        return
    session.run(
        "CREATE CONSTRAINT message_id IF NOT EXISTS "
        "FOR (m:Message) REQUIRE m.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT chat_id IF NOT EXISTS "
        "FOR (c:Chat) REQUIRE c.chat_id IS UNIQUE"
    )
    _schema_ensured = True


def _merge_message(session, msg: dict) -> None:
    session.run(
        """
        MERGE (m:Message {id: $id})
        SET m.chat_id = $chat_id, m.role = $role, m.content = $content,
            m.ts = $ts, m.kind = $kind
        MERGE (c:Chat {chat_id: $chat_id})
        MERGE (m)-[:IN_CHAT]->(c)
        """,
        id=msg["id"], chat_id=msg["chat_id"], role=msg["role"],
        content=msg["content"], ts=msg["ts"], kind=msg.get("kind", "chat"),
    )
    if msg.get("reply_to_id"):
        # MERGE (not MATCH) the parent so a reply whose parent hasn't been
        # projected yet (out-of-order ts, or a parent lost to a Neo4j outage)
        # still gets its edge — the parent's own row fills in its props later.
        session.run(
            """
            MATCH (m:Message {id: $id})
            MERGE (p:Message {id: $parent})
            MERGE (m)-[:REPLIES_TO]->(p)
            """,
            id=msg["id"], parent=msg["reply_to_id"],
        )


def _fetch_registry(session) -> list[tuple[str, tuple[str, ...]]]:
    """Canonical entity names + aliases across every label, casefolded surface
    forms cached for reuse. Returns [(canonical_name, (surface_cf, ...)), ...]."""
    global _registry_cache
    now = time.monotonic()
    if _registry_cache is not None and now - _registry_cache[0] < _REGISTRY_TTL_S:
        return _registry_cache[1]
    rows = session.run(
        "MATCH (e) WHERE e.canonical_name IS NOT NULL "
        "RETURN e.canonical_name AS name, coalesce(e.aliases, []) AS aliases "
        "LIMIT $limit",
        limit=_REGISTRY_LIMIT,
    )
    registry: list[tuple[str, tuple[str, ...]]] = []
    for r in rows:
        name = r["name"]
        if not name:
            continue
        surfaces = {name, *(r["aliases"] or [])}
        cf = tuple(s.casefold() for s in surfaces if s and len(s) >= _MIN_ENTITY_LEN)
        if cf:
            registry.append((name, cf))
    _registry_cache = (now, registry)
    return registry


def _mentioned_names(text: str, registry: list[tuple[str, tuple[str, ...]]]) -> list[str]:
    """Canonical names whose name/alias occurs in ``text`` (word-boundary for
    ASCII, substring for Korean — reusing ``ignore._matches_target``)."""
    if not text:
        return []
    t = text.casefold()
    return [name for name, surfaces in registry
            if any(_matches_target(s, t) for s in surfaces)]


def _link_mentions(session, msg: dict, registry: list[tuple[str, tuple[str, ...]]]) -> None:
    names = _mentioned_names(msg.get("content") or "", registry)
    if not names:
        return
    # One UNWIND query for all matches instead of a query per entity (N+1).
    session.run(
        """
        MATCH (m:Message {id: $id})
        UNWIND $names AS name
        MATCH (e {canonical_name: name})
        MERGE (m)-[:MENTIONS]->(e)
        """,
        id=msg["id"], names=names,
    )


def _should_link_mentions(msg: dict) -> bool:
    """Mentions are linked on real user questions only — the shared policy that
    makes the live path and a full rebuild produce the same graph."""
    return msg.get("role") == "user" and msg.get("kind", "chat") == "chat"


def _project_one(session, msg: dict, registry: list[tuple[str, tuple[str, ...]]] | None) -> None:
    """Single source of truth for a message's nodes/edges (used by every path)."""
    _merge_message(session, msg)
    if registry is not None and _should_link_mentions(msg):
        _link_mentions(session, msg, registry)


def project_message(msg: dict, link_mentions: bool = True) -> None:
    """Project a single message row (best-effort — swallows and logs failures)."""
    try:
        with get_driver().session() as session:
            _ensure_schema(session)
            registry = _fetch_registry(session) if link_mentions else None
            _project_one(session, msg, registry)
    except Exception:
        logger.exception("project_message failed for %s", msg.get("id"))


def project_exchange(chat_id: str, user_id: str, assistant_id: str) -> None:
    """Project a freshly-stored user/assistant exchange. Best-effort.

    Both rows are fetched in one query and run through the shared ``_project_one``
    policy, so the live graph matches what ``reproject_all`` would build."""
    msgs = conv.get_messages([user_id, assistant_id])
    if len(msgs) < 2:
        return
    try:
        with get_driver().session() as session:
            _ensure_schema(session)
            registry = _fetch_registry(session)
            for msg in msgs:
                _project_one(session, msg, registry)
    except Exception:
        logger.exception("project_exchange failed for chat %s", chat_id)


def reproject_all(link_mentions: bool = True) -> int:
    """Rebuild the Message subgraph from SQLite. Returns the count projected.

    A true rebuild: every ``:Message`` is detached and deleted first, then the
    store is replayed through the same ``_project_one`` policy as the live path.
    Deleting first is what lets it purge messages cleared from SQLite (a MERGE-only
    pass could only ever add)."""
    count = 0
    with get_driver().session() as session:
        _ensure_schema(session)
        session.run("MATCH (m:Message) DETACH DELETE m")
        registry = _fetch_registry(session) if link_mentions else None
        for msg in conv.iter_all_messages():
            _project_one(session, msg, registry)
            count += 1
    return count


def delete_chat(chat_id: str | None = None) -> None:
    """Remove a chat's messages (or all, when ``chat_id`` is None) from the graph
    so a SQLite clear propagates. Best-effort — logs, never raises."""
    try:
        with get_driver().session() as session:
            if chat_id is None:
                session.run("MATCH (m:Message) DETACH DELETE m")
            else:
                session.run(
                    "MATCH (m:Message {chat_id: $chat_id}) DETACH DELETE m",
                    chat_id=chat_id,
                )
    except Exception:
        logger.exception("delete_chat failed for %s", chat_id or "all")


def messages_about_entity(entity: str, n: int = 10) -> list[dict]:
    """Messages that MENTION an entity (by canonical_name), newest-first.
    The payoff of projection: query conversations through the news graph."""
    try:
        with get_driver().session() as session:
            rows = session.run(
                """
                MATCH (m:Message)-[:MENTIONS]->(e {canonical_name: $name})
                RETURN m.id AS id, m.chat_id AS chat_id, m.role AS role,
                       m.content AS content, m.ts AS ts
                ORDER BY m.ts DESC LIMIT $n
                """,
                name=entity, n=n,
            )
            return [dict(r) for r in rows]
    except Exception:
        logger.exception("messages_about_entity failed for %s", entity)
        return []
