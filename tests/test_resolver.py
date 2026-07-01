from unittest.mock import MagicMock, patch

from newsparser.claude.output_parser import EntityUpdate
from newsparser.claude.runner import ClaudeError
from newsparser.graph.resolver import (
    _build_prompt,
    _parse_response,
    fetch_registry,
    resolve_entities,
)


def _entity(name, label="Company", aliases=None):
    return EntityUpdate(op="NEW", label=label, name=name, aliases=aliases or [])


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
