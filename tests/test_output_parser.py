import textwrap
from newsparser.claude.output_parser import parse_graph_updates, EntityUpdate, RelationUpdate

SAMPLE_REPORT = textwrap.dedent("""\
    # Cycle 2026-05-05 00:00 KST

    ## New developments
    - [importance: 0.85] **Fed cuts rates.** Emergency 50bp cut.

    ## Graph updates
    ### Entities
    - NEW | Institution | Fed | aliases: [연준, Federal Reserve]
    - NEW | Event | FOMC 5월 회의 | aliases: [FOMC May]
    - UPDATE | Market | KOSPI

    ### Relations
    - NEW | FOMC 5월 회의 --IMPACTS[conf:0.85, impact:0.80]--> KOSPI | 금리인하 서프라이즈
    - NEW | FOMC 5월 회의 --INFLUENCES[conf:0.80, impact:0.70]--> KRW/USD | 달러 약세 압력
    - UPDATE | 미국금리 --IMPACTS[conf:0.90, impact:0.85]--> 반도체섹터

    ## Open threads
    - Watch for BOK response
""")

def test_parse_entities():
    entities, _ = parse_graph_updates(SAMPLE_REPORT)
    assert len(entities) == 3
    assert entities[0] == EntityUpdate(op="NEW", label="Institution", name="Fed", aliases=["연준", "Federal Reserve"])
    assert entities[1] == EntityUpdate(op="NEW", label="Event", name="FOMC 5월 회의", aliases=["FOMC May"])
    assert entities[2] == EntityUpdate(op="UPDATE", label="Market", name="KOSPI", aliases=[])

def test_parse_relations():
    _, relations = parse_graph_updates(SAMPLE_REPORT)
    assert len(relations) == 3
    assert relations[0] == RelationUpdate(
        op="NEW", subject="FOMC 5월 회의", predicate="IMPACTS", obj="KOSPI",
        confidence=0.85, impact_score=0.80, predicate_text="금리인하 서프라이즈"
    )
    assert relations[1].predicate == "INFLUENCES"
    assert relations[2].op == "UPDATE"
    assert relations[2].subject == "미국금리"

def test_parse_empty_report():
    entities, relations = parse_graph_updates("# Cycle\n## New developments\n- nothing")
    assert entities == []
    assert relations == []


def test_relation_with_src_captures_indices():
    report = (
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7, src:A001,A007]--> SPX | rate decision\n"
    )
    entities, relations = parse_graph_updates(report)
    assert len(relations) == 1
    r = relations[0]
    assert r.subject == "Fed"
    assert r.obj == "SPX"
    assert r.predicate == "IMPACTS"
    assert r.source_indices == ["A001", "A007"]


def test_relation_without_src_keeps_empty_indices():
    report = (
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7]--> SPX | rate decision\n"
    )
    entities, relations = parse_graph_updates(report)
    assert relations[0].source_indices == []


def test_relation_with_single_src_index():
    report = (
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | OpenAI --ANNOUNCED[conf:0.95, impact:0.6, src:A003]--> GPT-5 | release\n"
    )
    entities, relations = parse_graph_updates(report)
    assert relations[0].source_indices == ["A003"]
