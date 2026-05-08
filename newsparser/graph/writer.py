from newsparser.claude.output_parser import EntityUpdate, RelationUpdate
from newsparser.graph.neo4j_client import get_driver


def upsert_entity(entity: EntityUpdate, cycle_id: str, category: str | None = None) -> None:
    with get_driver().session() as session:
        session.run(
            f"MERGE (e:{entity.label} {{canonical_name: $name}}) "
            "ON CREATE SET e.first_seen = datetime(), e.mention_count = 1, "
            "  e.aliases = $aliases, e.category = $category "
            "ON MATCH SET e.mention_count = e.mention_count + 1, "
            "  e.category = coalesce(e.category, $category) "
            "SET e.last_seen = datetime()",
            name=entity.name,
            aliases=entity.aliases,
            category=category,
        )


def upsert_relation(rel: RelationUpdate, cycle_id: str, category: str | None = None) -> None:
    with get_driver().session() as session:
        session.run(
            "MATCH (a {canonical_name: $subject}) "
            "MATCH (b {canonical_name: $obj}) "
            f"MERGE (a)-[r:{rel.predicate}]->(b) "
            "ON CREATE SET r.first_seen = datetime(), r.confidence = $conf, "
            "  r.impact_score = $impact, r.source_cycles = [$cycle_id], "
            "  r.predicate_text = $text, r.category = $category "
            "ON MATCH SET r.impact_score = 0.85 * r.impact_score + 0.15 * $impact, "
            "  r.source_cycles = r.source_cycles + [$cycle_id], "
            "  r.category = coalesce(r.category, $category) "
            "SET r.last_seen = datetime()",
            subject=rel.subject, obj=rel.obj,
            conf=rel.confidence, impact=rel.impact_score,
            cycle_id=cycle_id, text=rel.predicate_text,
            category=category,
        )


def apply_graph_updates(
    entities: list[EntityUpdate],
    relations: list[RelationUpdate],
    cycle_id: str,
    category: str | None = None,
) -> None:
    for entity in entities:
        upsert_entity(entity, cycle_id, category)
    for relation in relations:
        upsert_relation(relation, cycle_id, category)
