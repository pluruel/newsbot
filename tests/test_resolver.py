from unittest.mock import MagicMock, patch

import newsparser.graph.resolver as resolver_mod
from newsparser.claude.output_parser import EntityUpdate, RelationUpdate
from newsparser.claude.runner import ClaudeError
from newsparser.graph.resolver import (
    _build_prompt,
    _deterministic_matches,
    _lucene_escape,
    _normalize,
    _parse_response,
    ensure_entity_index,
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


def test_parse_response_returns_self_name_resolution():
    """An answer equal to the candidate's name is returned — resolve_entities
    needs it for label-only overrides and filters true no-ops itself."""
    candidates = [_entity("Tesla")]
    assert _parse_response("C1: Tesla", candidates, {"Tesla"}) == {"Tesla": "Tesla"}


def test_parse_response_ignores_unknown_candidate_id():
    candidates = [_entity("OpenAI")]
    assert _parse_response("C99: Tesla", candidates, {"Tesla"}) == {}


def test_normalize_collapses_case_and_spacing():
    assert _normalize("NVIDIA") == _normalize("Nvidia")
    assert _normalize("SK hynix") == _normalize("SK Hynix")
    # punctuation / spacing dropped, CJK kept intact
    assert _normalize("SK 하이닉스!") == "sk하이닉스"
    assert _normalize("Micron Technology") != _normalize("Micron")


def _reg(name, label="Company", aliases=None):
    return {"name": name, "aliases": aliases or [], "label": label}


def test_deterministic_matches_case_variant():
    rename, remaining = _deterministic_matches([_entity("NVIDIA")], [_reg("Nvidia")])
    assert rename == {"NVIDIA": ("Nvidia", "Company")}
    assert remaining == []


def test_deterministic_matches_spacing_variant():
    rename, remaining = _deterministic_matches([_entity("SK Hynix")], [_reg("SK hynix")])
    assert rename == {"SK Hynix": ("SK hynix", "Company")}
    assert remaining == []


def test_deterministic_matches_name_via_registry_alias():
    """The candidate's own NAME hitting a registry alias is confirmed."""
    reg = [_reg("SK hynix", aliases=["SK하이닉스", "에스케이하이닉스"])]
    cand = _entity("SK하이닉스")
    rename, remaining = _deterministic_matches([cand], reg)
    assert rename == {"SK하이닉스": ("SK hynix", "Company")}
    assert remaining == []


def test_deterministic_matches_alias_only_hit_defers_to_haiku():
    """A match reachable only through the candidate's aliases is NOT
    auto-confirmed — one noisy LLM-extracted alias ('삼성' on a Samsung
    Biologics candidate) must not silently merge two distinct entities."""
    reg = [_reg("Samsung Electronics", aliases=["삼성"])]
    cand = _entity("Samsung Biologics", aliases=["삼성"])
    rename, remaining = _deterministic_matches([cand], reg)
    assert rename == {}
    assert [c.name for c in remaining] == ["Samsung Biologics"]


def test_deterministic_matches_label_override_same_name():
    """Same name, different label (Anthropic in both Company and Institution):
    keep the name, adopt the existing node's label."""
    reg = [_reg("Anthropic", label="Institution")]
    rename, remaining = _deterministic_matches([_entity("Anthropic", label="Company")], reg)
    assert rename == {"Anthropic": ("Anthropic", "Institution")}
    assert remaining == []


def test_deterministic_matches_defers_ambiguous_multi_hit():
    """A candidate matching two distinct canonical names is left for Haiku."""
    reg = [
        _reg("Micron", aliases=["마이크론"]),
        _reg("Micron Technology", aliases=["마이크론"]),
    ]
    rename, remaining = _deterministic_matches([_entity("마이크론")], reg)
    assert rename == {}
    assert [c.name for c in remaining] == ["마이크론"]


def test_deterministic_matches_self_match_dropped_from_haiku():
    """A candidate already equal to its canonical AND label resolves to itself:
    no rename, and not sent to Haiku."""
    rename, remaining = _deterministic_matches([_entity("Nvidia")], [_reg("Nvidia")])
    assert rename == {}
    assert remaining == []


def test_deterministic_matches_no_hit_goes_to_haiku():
    rename, remaining = _deterministic_matches([_entity("OpenAI")], [_reg("Tesla", aliases=["TSLA"])])
    assert rename == {}
    assert [c.name for c in remaining] == ["OpenAI"]


def test_parse_response_accepts_unique_normalized_match():
    """Haiku echoes 'SK Hynix' but the registry stores 'SK hynix' — a unique
    normalized hit is accepted."""
    candidates = [_entity("에스케이하이닉스")]
    rename = _parse_response("C1: SK Hynix", candidates, {"SK hynix"})
    assert rename == {"에스케이하이닉스": "SK hynix"}


def test_parse_response_rejects_ambiguous_normalized_match():
    candidates = [_entity("마이크론")]
    # two registry names collapse to the same normalized key → ambiguous
    rename = _parse_response("C1: micron", candidates, {"Micron", "MICRON"})
    assert rename == {}


def test_resolve_entities_deterministic_match_skips_haiku():
    with patch("newsparser.graph.resolver.fetch_registry",
               return_value=([{"name": "Nvidia", "aliases": [], "label": "Company"}], True)), \
         patch("newsparser.graph.resolver.ask_haiku") as mock_run:
        rename = resolve_entities([_entity("NVIDIA", label="Company")])
    mock_run.assert_not_called()
    assert rename == {"NVIDIA": ("Nvidia", "Company")}


def test_resolve_entities_cross_label_override():
    """Company candidate resolves onto an existing Institution node of the same
    name — the Company/Institution group is crossed and the label overridden."""
    with patch("newsparser.graph.resolver.fetch_registry",
               return_value=([{"name": "Anthropic", "aliases": [], "label": "Institution"}], True)), \
         patch("newsparser.graph.resolver.ask_haiku") as mock_run:
        rename = resolve_entities([_entity("Anthropic", label="Company")])
    mock_run.assert_not_called()
    assert rename == {"Anthropic": ("Anthropic", "Institution")}


def test_resolve_entities_incomplete_registry_routes_all_to_haiku():
    """complete=False (full-text degraded/populating) must disable the
    deterministic pass — an incomplete registry can make a wrong match look
    unambiguous. All candidates go to Haiku instead."""
    with patch("newsparser.graph.resolver.fetch_registry",
               return_value=([{"name": "Nvidia", "aliases": [], "label": "Company"}], False)), \
         patch("newsparser.graph.resolver.ask_haiku", return_value="C1: Nvidia") as mock_run:
        rename = resolve_entities([_entity("NVIDIA", label="Company")])
    mock_run.assert_called_once()
    assert rename == {"NVIDIA": ("Nvidia", "Company")}


def test_resolve_entities_haiku_label_only_override():
    """Haiku answering the candidate's own name still carries the existing
    node's label — the write must MERGE onto that node, not fork a new label."""
    with patch("newsparser.graph.resolver.fetch_registry",
               return_value=([{"name": "Anthropic", "aliases": [], "label": "Institution"}], False)), \
         patch("newsparser.graph.resolver.ask_haiku", return_value="C1: Anthropic"):
        rename = resolve_entities([_entity("Anthropic", label="Company")])
    assert rename == {"Anthropic": ("Anthropic", "Institution")}


def test_resolve_entities_haiku_self_answer_same_label_is_noop():
    with patch("newsparser.graph.resolver.fetch_registry",
               return_value=([{"name": "Anthropic", "aliases": [], "label": "Company"}], False)), \
         patch("newsparser.graph.resolver.ask_haiku", return_value="C1: Anthropic"):
        rename = resolve_entities([_entity("Anthropic", label="Company")])
    assert rename == {}


def test_resolve_entities_skips_names_extracted_under_multiple_groups():
    """The same name under two label groups can't be resolved safely (the
    name-keyed rename map and label-less relation endpoints would stomp one
    group's meaning with the other's) — both are left as-is."""
    with patch("newsparser.graph.resolver.fetch_registry",
               return_value=([{"name": "비트코인 시장", "aliases": ["비트코인"], "label": "Market"}], True)) as mock_fetch, \
         patch("newsparser.graph.resolver.ask_haiku") as mock_run:
        rename = resolve_entities([_entity("비트코인", label="Market"),
                                   _entity("비트코인", label="Indicator")])
    assert rename == {}
    mock_fetch.assert_not_called()
    mock_run.assert_not_called()


def test_build_prompt_includes_registry_and_candidates():
    registry = [{"name": "Tesla", "aliases": ["TSLA", "테슬라"], "label": "Company"}]
    candidates = [_entity("테슬라", aliases=["TSLA"])]
    prompt = _build_prompt(registry, candidates)
    assert "Tesla" in prompt
    assert "테슬라" in prompt
    assert "C1." in prompt
    assert "[Company]" in prompt
    # No Event candidates → no event-identity hint.
    assert "±2일" not in prompt


def test_build_prompt_adds_event_hint_for_event_candidates():
    registry = [{"name": "Claude Fable 5 출시 2026-06-09", "aliases": [], "label": "Event"}]
    candidates = [_entity("Mythos 5 발표 2026-06-09", label="Event")]
    prompt = _build_prompt(registry, candidates)
    assert "±2일" in prompt


def test_resolve_entities_skips_haiku_when_registry_empty():
    with patch("newsparser.graph.resolver.fetch_registry", return_value=([], True)), \
         patch("newsparser.graph.resolver.ask_haiku") as mock_run:
        rename = resolve_entities([_entity("OpenAI")])
    mock_run.assert_not_called()
    assert rename == {}


def test_resolve_entities_groups_candidates_by_label_group_in_separate_calls():
    calls = []

    def fake_fetch(labels, candidates=None):
        tag = "-".join(labels)
        return [{"name": f"Existing-{tag}", "aliases": [], "label": labels[0]}], True

    def fake_run(prompt, *a, **kw):
        calls.append(prompt)
        return "C1: NEW"

    with patch("newsparser.graph.resolver.fetch_registry", side_effect=fake_fetch), \
         patch("newsparser.graph.resolver.ask_haiku", side_effect=fake_run):
        resolve_entities([_entity("A", label="Company"), _entity("B", label="Person")])
    # Company and Person are different groups → two separate calls.
    assert len(calls) == 2


def test_resolve_entities_company_and_institution_share_one_call():
    """Company + Institution collapse into a single 'org' group → one registry
    fetch and one Haiku call spanning both."""
    fetch_calls = []

    def fake_fetch(labels, candidates=None):
        fetch_calls.append(labels)
        return [{"name": "Goldman Sachs", "aliases": [], "label": "Institution"}], True

    def fake_run(prompt, *a, **kw):
        return "C1: NEW\nC2: NEW"

    with patch("newsparser.graph.resolver.fetch_registry", side_effect=fake_fetch), \
         patch("newsparser.graph.resolver.ask_haiku", side_effect=fake_run) as mock_run:
        resolve_entities([_entity("Acme", label="Company"),
                          _entity("Some Bank", label="Institution")])
    assert len(fetch_calls) == 1
    assert set(fetch_calls[0]) == {"Company", "Institution"}
    assert mock_run.call_count == 1


def test_resolve_entities_applies_rename_from_haiku_response():
    with patch("newsparser.graph.resolver.fetch_registry",
               return_value=([{"name": "Tesla", "aliases": ["TSLA"], "label": "Company"}], True)), \
         patch("newsparser.graph.resolver.ask_haiku", return_value="C1: Tesla"):
        rename = resolve_entities([_entity("테슬라", label="Company")])
    assert rename == {"테슬라": ("Tesla", "Company")}


def test_resolve_entities_tolerates_haiku_failure_after_retries_exhausted():
    with patch("newsparser.graph.resolver.fetch_registry",
               return_value=([{"name": "Tesla", "aliases": []}], True)), \
         patch("newsparser.graph.resolver.ask_haiku", side_effect=ClaudeError("boom")) as mock_run, \
         patch("newsparser.graph.resolver.time.sleep"):
        rename = resolve_entities([_entity("테슬라")])
    assert rename == {}
    assert mock_run.call_count == 3  # _RETRIES


def test_resolve_entities_retries_then_succeeds():
    with patch("newsparser.graph.resolver.fetch_registry",
               return_value=([{"name": "Tesla", "aliases": [], "label": "Company"}], True)), \
         patch("newsparser.graph.resolver.ask_haiku",
               side_effect=[ClaudeError("timed out"), "C1: Tesla"]) as mock_run, \
         patch("newsparser.graph.resolver.time.sleep"):
        rename = resolve_entities([_entity("테슬라")])
    assert rename == {"테슬라": ("Tesla", "Company")}
    assert mock_run.call_count == 2


def test_resolve_entities_tolerates_registry_fetch_failure():
    with patch("newsparser.graph.resolver.fetch_registry", side_effect=RuntimeError("neo4j down")), \
         patch("newsparser.graph.resolver.ask_haiku") as mock_run:
        rename = resolve_entities([_entity("테슬라")])
    mock_run.assert_not_called()
    assert rename == {}


def test_fetch_registry_queries_by_labels(monkeypatch):
    monkeypatch.setenv("NEO4J_PASSWORD", "testpass")
    fake_session = MagicMock()
    fake_session.run.return_value = [{"name": "Tesla", "aliases": ["TSLA"], "label": "Company"}]
    fake_driver = MagicMock()
    fake_driver.session.return_value.__enter__.return_value = fake_session
    with patch("newsparser.graph.resolver.get_driver", return_value=fake_driver):
        rows, complete = fetch_registry(["Company", "Institution"])
    assert rows == [{"name": "Tesla", "aliases": ["TSLA"], "label": "Company"}]
    # candidate-less scan has no targeted coverage → never complete
    assert complete is False
    _, kwargs = fake_session.run.call_args
    assert kwargs["labels"] == ["Company", "Institution"]


def _targeted_driver(monkeypatch, dispatch):
    """Build a mocked driver whose session.run routes by query keyword through
    `dispatch(query) -> rows`. Resets the module-level index-ensured flag."""
    monkeypatch.setenv("NEO4J_PASSWORD", "testpass")
    resolver_mod._index_ensured = False

    def fake_run(query, **kw):
        return dispatch(query)

    fake_session = MagicMock()
    fake_session.run.side_effect = fake_run
    fake_driver = MagicMock()
    fake_driver.session.return_value.__enter__.return_value = fake_session
    return fake_driver, fake_session


def test_fetch_registry_targeted_unions_base_and_fulltext(monkeypatch):
    def dispatch(q):
        if "CREATE FULLTEXT INDEX" in q:
            return []
        if "queryNodes" in q:
            return [{"name": "Micron Technology", "aliases": [], "label": "Company"}]
        if "mention_count" in q:
            return [{"name": "Nvidia", "aliases": [], "label": "Company"}]
        return []

    driver, _ = _targeted_driver(monkeypatch, dispatch)
    with patch("newsparser.graph.resolver.get_driver", return_value=driver):
        rows, complete = fetch_registry(["Company", "Institution"], [_entity("마이크론")])
    assert {r["name"] for r in rows} == {"Nvidia", "Micron Technology"}
    assert complete is True


def test_fetch_registry_targeted_degrades_to_base_on_fulltext_failure(monkeypatch):
    def dispatch(q):
        if "queryNodes" in q:
            raise RuntimeError("index not found")
        if "CREATE FULLTEXT INDEX" in q:
            return []
        if "mention_count" in q:
            return [{"name": "Nvidia", "aliases": [], "label": "Company"}]
        return []

    driver, _ = _targeted_driver(monkeypatch, dispatch)
    with patch("newsparser.graph.resolver.get_driver", return_value=driver):
        rows, complete = fetch_registry(["Company"], [_entity("마이크론")])
    # No exception; full-text loss leaves just the top-N base, and the registry
    # is flagged incomplete so the deterministic pass stays off.
    assert {r["name"] for r in rows} == {"Nvidia"}
    assert complete is False


def test_fetch_registry_merges_same_name_rows_by_mention_count(monkeypatch):
    """The same canonical_name from two nodes (label fragmentation): the
    more-established row's label wins regardless of arrival order, and the
    aliases are unioned so the loser's surface forms stay matchable."""
    def dispatch(q):
        if "CREATE FULLTEXT INDEX" in q:
            return []
        if "queryNodes" in q:
            # full-text row arrives second but is more established
            return [{"name": "Citi", "aliases": ["씨티그룹"], "label": "Company",
                     "mention_count": 40}]
        if "mention_count" in q:
            return [{"name": "Citi", "aliases": ["Citigroup"], "label": "Institution",
                     "mention_count": 3}]
        return []

    driver, _ = _targeted_driver(monkeypatch, dispatch)
    with patch("newsparser.graph.resolver.get_driver", return_value=driver):
        rows, _ = fetch_registry(["Company", "Institution"], [_entity("Citi")])
    assert len(rows) == 1
    assert rows[0]["label"] == "Company"
    assert set(rows[0]["aliases"]) == {"씨티그룹", "Citigroup"}


def test_fetch_registry_targeted_folds_in_recent_events(monkeypatch):
    seen = []

    def dispatch(q):
        if "duration" in q:
            seen.append(q)
            return [{"name": "Iran strike 2026-06-30", "aliases": [], "label": "Event"}]
        if "queryNodes" in q or "CREATE FULLTEXT INDEX" in q or "mention_count" in q:
            return []
        return []

    driver, _ = _targeted_driver(monkeypatch, dispatch)
    with patch("newsparser.graph.resolver.get_driver", return_value=driver):
        rows, _ = fetch_registry(["Event"], [_entity("이란 공습", label="Event")])
    assert {r["name"] for r in rows} == {"Iran strike 2026-06-30"}
    # recent-event bucket must be bounded (last_seen can collapse to all events)
    assert "ORDER BY e.last_seen DESC LIMIT" in seen[0]


def test_fetch_registry_no_candidates_uses_top_n_fallback(monkeypatch):
    """The candidate-less path stays on the flat top-N scan (no index needed)
    and reports incomplete — it has no candidate-targeted coverage."""
    calls = []

    def dispatch(q):
        calls.append(q)
        return [{"name": "Tesla", "aliases": [], "label": "Company"}]

    driver, _ = _targeted_driver(monkeypatch, dispatch)
    with patch("newsparser.graph.resolver.get_driver", return_value=driver):
        rows, complete = fetch_registry(["Company"])
    assert [r["name"] for r in rows] == ["Tesla"]
    assert complete is False
    assert all("queryNodes" not in q and "CREATE FULLTEXT" not in q for q in calls)


def test_ensure_entity_index_is_idempotent_per_process(monkeypatch):
    resolver_mod._index_ensured = False
    session = MagicMock()
    assert ensure_entity_index(session) is True
    assert ensure_entity_index(session) is True
    # first call: CREATE + awaitIndex (population barrier); second call: cached
    assert session.run.call_count == 2
    stmt = session.run.call_args_list[0].args[0]
    assert "CREATE FULLTEXT INDEX" in stmt and "entity_names" in stmt
    assert "awaitIndex" in session.run.call_args_list[1].args[0]


def test_ensure_entity_index_reports_unusable_index(monkeypatch):
    resolver_mod._index_ensured = False
    session = MagicMock()
    session.run.side_effect = RuntimeError("no fulltext support")
    assert ensure_entity_index(session) is False
    # not cached as ensured — the next call retries
    assert resolver_mod._index_ensured is False


def test_lucene_escape_escapes_special_chars():
    assert _lucene_escape("AT&T (Inc.)") == r"AT\&T \(Inc.\)"
    assert _lucene_escape("plain name") == "plain name"


def test_lucene_escape_neutralizes_boolean_operators():
    """Bare AND/OR/NOT are Lucene operators and raise ParseException in
    operator-illegal positions — lowercase them into literal terms."""
    assert _lucene_escape("AND Digital") == "and Digital"
    assert _lucene_escape("S OR P") == "S or P"
    assert _lucene_escape("Brandy") == "Brandy"  # substring untouched


def test_prepare_graph_updates_renames_entities_and_relations(tmp_path):
    entities = [_entity("테슬라")]
    relations = [_relation("테슬라", "Robotaxi")]
    with patch("newsparser.graph.resolver.resolve_entities",
               return_value={"테슬라": ("Tesla", "Company")}):
        entities, relations = prepare_graph_updates(entities, relations, tmp_path)
    assert entities[0].name == "Tesla"
    assert relations[0].subject == "Tesla"


def test_prepare_graph_updates_applies_label_override(tmp_path):
    entities = [_entity("Anthropic", label="Company")]
    with patch("newsparser.graph.resolver.resolve_entities",
               return_value={"Anthropic": ("Anthropic", "Institution")}):
        entities, _ = prepare_graph_updates(entities, [], tmp_path)
    assert entities[0].name == "Anthropic"
    assert entities[0].label == "Institution"


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
