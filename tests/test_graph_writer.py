import os
import pytest
from newsparser.claude.output_parser import EntityUpdate, RelationUpdate
from newsparser.graph.writer import apply_graph_updates
from newsparser.graph.neo4j_client import get_driver, close_driver

@pytest.fixture(autouse=True)
def neo4j_clean():
    os.environ.setdefault("NEO4J_PASSWORD", "testpass")
    with get_driver().session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    yield
    close_driver()

def test_upsert_entity_creates_node():
    entity = EntityUpdate(op="NEW", label="Institution", name="Fed", aliases=["연준"])
    apply_graph_updates([entity], [], "cycle-001")
    with get_driver().session() as s:
        row = s.run("MATCH (e:Institution {canonical_name: 'Fed'}) RETURN e.mention_count AS mc").single()
    assert row["mc"] == 1

def test_upsert_entity_increments_mention_count():
    entity = EntityUpdate(op="NEW", label="Institution", name="Fed", aliases=[])
    apply_graph_updates([entity], [], "cycle-001")
    apply_graph_updates([entity], [], "cycle-002")
    with get_driver().session() as s:
        row = s.run("MATCH (e:Institution {canonical_name: 'Fed'}) RETURN e.mention_count AS mc").single()
    assert row["mc"] == 2

def test_upsert_relation_creates_edge():
    entities = [
        EntityUpdate(op="NEW", label="Event", name="FOMC 5월", aliases=[]),
        EntityUpdate(op="NEW", label="Market", name="KOSPI", aliases=[]),
    ]
    rel = RelationUpdate(op="NEW", subject="FOMC 5월", predicate="IMPACTS",
                         obj="KOSPI", confidence=0.85, impact_score=0.80)
    apply_graph_updates(entities, [rel], "cycle-001")
    with get_driver().session() as s:
        row = s.run(
            "MATCH (:Event {canonical_name: 'FOMC 5월'})-[r:IMPACTS]->(:Market {canonical_name: 'KOSPI'}) "
            "RETURN r.impact_score AS impact"
        ).single()
    assert row["impact"] == pytest.approx(0.80)

def test_upsert_relation_ema_update():
    entities = [
        EntityUpdate(op="NEW", label="Event", name="FOMC 5월", aliases=[]),
        EntityUpdate(op="NEW", label="Market", name="KOSPI", aliases=[]),
    ]
    rel1 = RelationUpdate(op="NEW", subject="FOMC 5월", predicate="IMPACTS",
                          obj="KOSPI", confidence=0.85, impact_score=0.80)
    rel2 = RelationUpdate(op="UPDATE", subject="FOMC 5월", predicate="IMPACTS",
                          obj="KOSPI", confidence=0.90, impact_score=1.0)
    apply_graph_updates(entities, [rel1], "cycle-001")
    apply_graph_updates([], [rel2], "cycle-002")
    with get_driver().session() as s:
        row = s.run(
            "MATCH ()-[r:IMPACTS]->() RETURN r.impact_score AS impact"
        ).single()
    expected = 0.85 * 0.80 + 0.15 * 1.0
    assert row["impact"] == pytest.approx(expected, abs=0.01)
