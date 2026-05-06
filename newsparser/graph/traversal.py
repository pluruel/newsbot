from newsparser.graph.neo4j_client import get_driver


def get_context(entity_name: str, days: int = 7) -> list[dict]:
    """Return 2-hop neighbors updated within last N days."""
    with get_driver().session() as session:
        result = session.run(
            "MATCH (e {canonical_name: $name})-[*1..2]-(related) "
            "WHERE related.last_seen >= datetime() - duration({days: $days}) "
            "RETURN DISTINCT related.canonical_name AS name, "
            "  labels(related)[0] AS label, related.mention_count AS mentions "
            "ORDER BY related.mention_count DESC LIMIT 20",
            name=entity_name, days=days,
        )
        return [dict(r) for r in result]


def get_influence_chain(entity_name: str) -> list[dict]:
    """Return influence chain up to 3 hops."""
    with get_driver().session() as session:
        result = session.run(
            "MATCH path = (e {canonical_name: $name})"
            "-[:IMPACTS|INFLUENCES*1..3]->(target) "
            "RETURN [n IN nodes(path) | n.canonical_name] AS chain, length(path) AS depth "
            "ORDER BY depth LIMIT 10",
            name=entity_name,
        )
        return [dict(r) for r in result]


def get_high_impact_recent(hours: int = 24) -> list[dict]:
    """Return relations with impact_score > 0.7 from last N hours."""
    with get_driver().session() as session:
        result = session.run(
            "MATCH (a)-[r]->(b) "
            "WHERE r.last_seen >= datetime() - duration({hours: $hours}) "
            "  AND r.impact_score > 0.7 "
            "RETURN a.canonical_name AS subject, type(r) AS predicate, "
            "  b.canonical_name AS object, r.impact_score AS impact "
            "ORDER BY r.impact_score DESC LIMIT 20",
            hours=hours,
        )
        return [dict(r) for r in result]


def format_context_for_claude(
    entity_name: str,
    neighbors: list[dict],
    chains: list[dict],
) -> str:
    """Format graph context as markdown for Claude prompt."""
    lines = [f"## Graph context for: {entity_name}", ""]
    if neighbors:
        lines.append("### Related entities (2-hop, recent)")
        for n in neighbors:
            lines.append(f"- {n['label']}: {n['name']} (mentions: {n['mentions']})")
    if chains:
        lines.append("\n### Influence chains")
        for c in chains:
            lines.append(f"- {' → '.join(c['chain'])}")
    return "\n".join(lines)
