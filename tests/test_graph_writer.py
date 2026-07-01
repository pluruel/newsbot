import os
import pytest
from newsparser.claude.output_parser import EntityUpdate, RelationUpdate
from newsparser.graph.writer import apply_graph_updates
from newsparser.graph.neo4j_client import get_driver, close_driver

@pytest.fixture(autouse=True)
def neo4j_clean():
    if os.environ.get("NEWSPARSER_TEST_NEO4J") != "1":
        pytest.skip(
            "test_graph_writer.py wipes the entire graph (MATCH (n) DETACH DELETE n) "
            "and is not safe to run against the live Neo4j. "
            "Set NEWSPARSER_TEST_NEO4J=1 with NEO4J_URI pointing at a disposable instance."
        )
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

def test_upsert_entity_sets_category():
    entity = EntityUpdate(op="NEW", label="Company", name="OpenAI", aliases=[])
    apply_graph_updates([entity], [], "tech-2026-05-07-12", category="tech")
    with get_driver().session() as s:
        row = s.run("MATCH (e:Company {canonical_name: 'OpenAI'}) RETURN e.category AS c").single()
    assert row["c"] == "tech"


def test_upsert_entity_unions_aliases_on_match():
    entity1 = EntityUpdate(op="NEW", label="Company", name="Tesla", aliases=["TSLA"])
    entity2 = EntityUpdate(op="NEW", label="Company", name="Tesla", aliases=["테슬라"])
    apply_graph_updates([entity1], [], "cycle-001")
    apply_graph_updates([entity2], [], "cycle-002")
    with get_driver().session() as s:
        row = s.run("MATCH (e:Company {canonical_name: 'Tesla'}) RETURN e.aliases AS aliases").single()
    assert sorted(row["aliases"]) == ["TSLA", "테슬라"]


def test_upsert_entity_does_not_overwrite_existing_category():
    entity = EntityUpdate(op="NEW", label="Company", name="OpenAI", aliases=[])
    apply_graph_updates([entity], [], "tech-2026-05-07-12", category="tech")
    apply_graph_updates([entity], [], "markets-2026-05-07-12", category="markets")
    with get_driver().session() as s:
        row = s.run("MATCH (e:Company {canonical_name: 'OpenAI'}) RETURN e.category AS c").single()
    # First-set wins (coalesce semantics)
    assert row["c"] == "tech"


def test_upsert_relation_sets_category():
    entities = [
        EntityUpdate(op="NEW", label="Company", name="OpenAI", aliases=[]),
        EntityUpdate(op="NEW", label="Company", name="Microsoft", aliases=[]),
    ]
    rel = RelationUpdate(op="NEW", subject="OpenAI", predicate="INFLUENCES",
                         obj="Microsoft", confidence=0.7, impact_score=0.6)
    apply_graph_updates(entities, [rel], "tech-2026-05-07-12", category="tech")
    with get_driver().session() as s:
        row = s.run(
            "MATCH ()-[r:INFLUENCES]->() RETURN r.category AS c"
        ).single()
    assert row["c"] == "tech"


def test_upsert_relation_sets_source_article_guids_on_create():
    entities = [
        EntityUpdate(op="NEW", label="Institution", name="Fed", aliases=[]),
        EntityUpdate(op="NEW", label="Indicator", name="SPX", aliases=[]),
    ]
    rel = RelationUpdate(
        op="NEW", subject="Fed", predicate="IMPACTS",
        obj="SPX", confidence=0.85, impact_score=0.7,
        source_article_guids=["guid-a", "guid-b"],
    )
    apply_graph_updates(entities, [rel], "markets-2026-05-09-12")
    with get_driver().session() as s:
        row = s.run(
            "MATCH ()-[r:IMPACTS]->() RETURN r.source_article_guids AS guids"
        ).single()
    assert sorted(row["guids"]) == ["guid-a", "guid-b"]


def test_upsert_relation_unions_source_article_guids_on_match():
    entities = [
        EntityUpdate(op="NEW", label="Institution", name="Fed", aliases=[]),
        EntityUpdate(op="NEW", label="Indicator", name="SPX", aliases=[]),
    ]
    rel1 = RelationUpdate(op="NEW", subject="Fed", predicate="IMPACTS",
                          obj="SPX", confidence=0.85, impact_score=0.7,
                          source_article_guids=["guid-a", "guid-b"])
    rel2 = RelationUpdate(op="UPDATE", subject="Fed", predicate="IMPACTS",
                          obj="SPX", confidence=0.9, impact_score=0.8,
                          source_article_guids=["guid-b", "guid-c"])
    apply_graph_updates(entities, [rel1], "markets-2026-05-09-12")
    apply_graph_updates([], [rel2], "markets-2026-05-09-18")
    with get_driver().session() as s:
        row = s.run(
            "MATCH ()-[r:IMPACTS]->() RETURN r.source_article_guids AS guids"
        ).single()
    assert sorted(row["guids"]) == ["guid-a", "guid-b", "guid-c"]


def test_upsert_relation_handles_empty_source_article_guids():
    entities = [
        EntityUpdate(op="NEW", label="Company", name="OpenAI", aliases=[]),
        EntityUpdate(op="NEW", label="Company", name="Microsoft", aliases=[]),
    ]
    rel = RelationUpdate(op="NEW", subject="OpenAI", predicate="COMPETES_WITH",
                         obj="Microsoft", confidence=0.7, impact_score=0.5)
    apply_graph_updates(entities, [rel], "tech-2026-05-09-12")
    with get_driver().session() as s:
        row = s.run(
            "MATCH ()-[r:COMPETES_WITH]->() RETURN r.source_article_guids AS guids"
        ).single()
    # Either [] or null is acceptable; we just want this not to crash.
    assert row["guids"] in ([], None)
