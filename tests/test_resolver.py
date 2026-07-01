from unittest.mock import MagicMock, patch

from newsparser.claude.output_parser import EntityUpdate, RelationUpdate
from newsparser.claude.runner import ClaudeError
from newsparser.graph.resolver import (
    _build_prompt,
    _parse_response,
    fetch_registry,
    prepare_graph_updates,
    resolve_entities,
)


def _entity(name, label="Company", aliases=None):
    return EntityUpdate(op="NEW", label=label, name=name, aliases=aliases or [])


def _relation(subject, obj, predicate="ANNOUNCED"):
    return RelationUpdate(op="NEW", subject=subject, predicate=predicate, obj=obj,
                           confidence=0.9, impact_score=0.8)


def test_parse_response_maps_matched_candidate():
    candidates = [_entity("테슬라")]
    rename = _parse_response("C1: Tesla", candidates, {"Tesla", "Samsung Electronics"})
    assert rename == {"테슬라": "Tesla"}


def test_parse_response_skips_new():
    candidates = [_entity("OpenAI")]
    assert _parse_response("C1: NEW", candidates, {"Tesla"}) == {}


def test_parse_response_rejects_hallucinated_canonical_name():
    """Model must echo the registry name exactly — anything else is a hallucination risk."""
    candidates = [_entity("테슬라")]
    assert _parse_response("C1: Tesla Inc", candidates, {"Tesla"}) == {}


def test_parse_response_ignores_unparseable_lines():
    candidates = [_entity("OpenAI")]
    assert _parse_response("garbage line with no colon", candidates, {"Tesla"}) == {}


def test_parse_response_no_op_when_answer_equals_candidate_name():
    candidates = [_entity("Tesla")]
    assert _parse_response("C1: Tesla", candidates, {"Tesla"}) == {}


def test_parse_response_ignores_unknown_candidate_id():
    candidates = [_entity("OpenAI")]
    assert _parse_response("C99: Tesla", candidates, {"Tesla"}) == {}


def test_build_prompt_includes_registry_and_candidates():
    registry = [{"name": "Tesla", "aliases": ["TSLA", "테슬라"]}]
    candidates = [_entity("테슬라", aliases=["TSLA"])]
    prompt = _build_prompt("Company", registry, candidates)
    assert "Tesla" in prompt
    assert "테슬라" in prompt
    assert "C1." in prompt


def test_resolve_entities_skips_haiku_when_registry_empty():
    with patch("newsparser.graph.resolver.fetch_registry", return_value=[]), \
         patch("newsparser.graph.resolver.run_claude") as mock_run:
        rename = resolve_entities([_entity("OpenAI")])
    mock_run.assert_not_called()
    assert rename == {}


def test_resolve_entities_groups_candidates_by_label_in_separate_calls():
    calls = []

    def fake_fetch(label):
        return [{"name": f"Existing-{label}", "aliases": []}]

    def fake_run(prompt, **kw):
        calls.append(prompt)
        return "C1: NEW"

    with patch("newsparser.graph.resolver.fetch_registry", side_effect=fake_fetch), \
         patch("newsparser.graph.resolver.run_claude", side_effect=fake_run):
        resolve_entities([_entity("A", label="Company"), _entity("B", label="Person")])
    assert len(calls) == 2


def test_resolve_entities_applies_rename_from_haiku_response():
    with patch("newsparser.graph.resolver.fetch_registry",
               return_value=[{"name": "Tesla", "aliases": ["TSLA"]}]), \
         patch("newsparser.graph.resolver.run_claude", return_value="C1: Tesla"):
        rename = resolve_entities([_entity("테슬라", label="Company")])
    assert rename == {"테슬라": "Tesla"}


def test_resolve_entities_tolerates_haiku_failure():
    with patch("newsparser.graph.resolver.fetch_registry",
               return_value=[{"name": "Tesla", "aliases": []}]), \
         patch("newsparser.graph.resolver.run_claude", side_effect=ClaudeError("boom")):
        rename = resolve_entities([_entity("테슬라")])
    assert rename == {}


def test_resolve_entities_tolerates_registry_fetch_failure():
    with patch("newsparser.graph.resolver.fetch_registry", side_effect=RuntimeError("neo4j down")), \
         patch("newsparser.graph.resolver.run_claude") as mock_run:
        rename = resolve_entities([_entity("테슬라")])
    mock_run.assert_not_called()
    assert rename == {}


def test_fetch_registry_queries_by_label(monkeypatch):
    monkeypatch.setenv("NEO4J_PASSWORD", "testpass")
    fake_session = MagicMock()
    fake_session.run.return_value = [{"name": "Tesla", "aliases": ["TSLA"]}]
    fake_driver = MagicMock()
    fake_driver.session.return_value.__enter__.return_value = fake_session
    with patch("newsparser.graph.resolver.get_driver", return_value=fake_driver):
        rows = fetch_registry("Company")
    assert rows == [{"name": "Tesla", "aliases": ["TSLA"]}]
    args, _ = fake_session.run.call_args
    assert "Company" in args[0]


def test_prepare_graph_updates_renames_entities_and_relations(tmp_path):
    entities = [_entity("테슬라")]
    relations = [_relation("테슬라", "Robotaxi")]
    with patch("newsparser.graph.resolver.resolve_entities",
               return_value={"테슬라": "Tesla"}):
        entities, relations = prepare_graph_updates(entities, relations, tmp_path)
    assert entities[0].name == "Tesla"
    assert relations[0].subject == "Tesla"


def test_prepare_graph_updates_noop_when_no_rename(tmp_path):
    entities = [_entity("OpenAI")]
    relations = [_relation("OpenAI", "GPT-5")]
    with patch("newsparser.graph.resolver.resolve_entities", return_value={}):
        entities, relations = prepare_graph_updates(entities, relations, tmp_path)
    assert entities[0].name == "OpenAI"
    assert relations[0].subject == "OpenAI"


def test_prepare_graph_updates_drops_ignored_entities_and_relations(tmp_path):
    (tmp_path / "me").mkdir(parents=True)
    (tmp_path / "me" / "ignore.md").write_text(
        "| 종류 | 대상 | 추가일 | 메모 |\n"
        "|------|------|--------|------|\n"
        "| entity | GPT-5 | 2026-06-28 |  |\n",
        encoding="utf-8",
    )
    entities = [_entity("OpenAI"), _entity("GPT-5", label="Event")]
    relations = [_relation("OpenAI", "GPT-5")]
    with patch("newsparser.graph.resolver.resolve_entities", return_value={}):
        entities, relations = prepare_graph_updates(entities, relations, tmp_path)
    assert all(e.name != "GPT-5" for e in entities)
    assert relations == []
    assert any(e.name == "OpenAI" for e in entities)


def test_prepare_graph_updates_no_ignore_file_keeps_everything(tmp_path):
    entities = [_entity("OpenAI"), _entity("GPT-5", label="Event")]
    relations = [_relation("OpenAI", "GPT-5")]
    with patch("newsparser.graph.resolver.resolve_entities", return_value={}):
        entities, relations = prepare_graph_updates(entities, relations, tmp_path)
    assert len(entities) == 2
    assert len(relations) == 1
