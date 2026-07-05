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
"""

import logging

from newsparser.graph.neo4j_client import get_driver
from newsparser.store import conversations as conv

logger = logging.getLogger(__name__)

# Cap how many registry names we scan per message and how long a name must be to
# be worth a substring match (avoids linking on 1-2 char noise).
_MIN_ENTITY_LEN = 2


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
        session.run(
            """
            MATCH (m:Message {id: $id})
            MATCH (p:Message {id: $parent})
            MERGE (m)-[:REPLIES_TO]->(p)
            """,
            id=msg["id"], parent=msg["reply_to_id"],
        )


def _registry_names(session) -> list[str]:
    """Canonical entity names already in the news graph (any label)."""
    rows = session.run(
        "MATCH (e) WHERE e.canonical_name IS NOT NULL RETURN e.canonical_name AS n"
    )
    return [r["n"] for r in rows if r["n"]]


def _link_mentions(session, msg: dict, names: list[str]) -> None:
    text = msg.get("content") or ""
    for name in names:
        if len(name) >= _MIN_ENTITY_LEN and name in text:
            session.run(
                """
                MATCH (m:Message {id: $id})
                MATCH (e {canonical_name: $name})
                MERGE (m)-[:MENTIONS]->(e)
                """,
                id=msg["id"], name=name,
            )


def project_message(msg: dict, link_mentions: bool = True) -> None:
    """Project a single message row (best-effort — swallows and logs failures)."""
    try:
        with get_driver().session() as session:
            _merge_message(session, msg)
            if link_mentions:
                _link_mentions(session, msg, _registry_names(session))
    except Exception:
        logger.exception("project_message failed for %s", msg.get("id"))


def project_exchange(chat_id: str, user_id: str, assistant_id: str) -> None:
    """Project a freshly-stored user/assistant exchange. Best-effort.

    Runs entity linking on the user turn only — the question carries the entities
    worth indexing; the assistant turn is linked via its REPLIES_TO edge."""
    user = conv.get_message(user_id)
    assistant = conv.get_message(assistant_id)
    if user is None or assistant is None:
        return
    try:
        with get_driver().session() as session:
            names = _registry_names(session)
            _merge_message(session, user)
            _link_mentions(session, user, names)
            _merge_message(session, assistant)
    except Exception:
        logger.exception("project_exchange failed for chat %s", chat_id)


def reproject_all(link_mentions: bool = True) -> int:
    """Rebuild the Message subgraph from SQLite. Returns the count projected.

    Idempotent (all MERGE). Parents are merged as bare nodes first if their edge
    is seen before the parent row, then filled in when the parent's own row is
    reached — so a single ordered pass is enough."""
    count = 0
    with get_driver().session() as session:
        names = _registry_names(session) if link_mentions else []
        for msg in conv.iter_all_messages():
            _merge_message(session, msg)
            if link_mentions:
                _link_mentions(session, msg, names)
            count += 1
    return count


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
