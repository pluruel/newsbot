from datetime import datetime

from newsparser.claude.output_parser import EntityUpdate, RelationUpdate
from newsparser.graph.neo4j_client import get_driver


def upsert_entity(entity: EntityUpdate, cycle_id: str) -> None:
    with get_driver().session() as session:
        session.run(
            f"MERGE (e:{entity.label} {{canonical_name: $name}}) "
            "ON CREATE SET e.first_seen = $now, e.mention_count = 1, e.aliases = $aliases "
            "ON MATCH SET e.mention_count = e.mention_count + 1 "
            "SET e.last_seen = $now",
            name=entity.name,
            now=datetime.utcnow().isoformat(),
            aliases=entity.aliases,
        )


def upsert_relation(rel: RelationUpdate, cycle_id: str) -> None:
    with get_driver().session() as session:
        session.run(
            "MATCH (a {canonical_name: $subject}) "
            "MATCH (b {canonical_name: $obj}) "
            f"MERGE (a)-[r:{rel.predicate}]->(b) "
            "ON CREATE SET r.first_seen = $now, r.confidence = $conf, "
            "  r.impact_score = $impact, r.source_cycles = [$cycle_id], "
            "  r.predicate_text = $text "
            "ON MATCH SET r.impact_score = 0.85 * r.impact_score + 0.15 * $impact, "
            "  r.source_cycles = r.source_cycles + [$cycle_id] "
            "SET r.last_seen = $now",
            subject=rel.subject, obj=rel.obj,
            now=datetime.utcnow().isoformat(),
            conf=rel.confidence, impact=rel.impact_score,
            cycle_id=cycle_id, text=rel.predicate_text,
        )


def apply_graph_updates(
    entities: list[EntityUpdate],
    relations: list[RelationUpdate],
    cycle_id: str,
) -> None:
    for entity in entities:
        upsert_entity(entity, cycle_id)
    for relation in relations:
        upsert_relation(relation, cycle_id)
