"""Haiku-backed entity name resolution.

Each cycle's LLM extracts entities blind to what's already in Neo4j, so the
same real-world entity can fragment across nodes ("Tesla" vs "테슬라" vs
"TSLA"). Before writing new entities, resolve each candidate against the
existing registry (same label) via a single batched Haiku call and rename it
to the existing canonical_name when it's the same entity.

Fails safe: any parse/lookup/call failure leaves names as-is (status quo
fragmentation, recoverable later) rather than risking a wrong merge (hard to
undo once relations are combined into one node).
"""
import logging
import random
import re
import time
import unicodedata
from pathlib import Path

from newsparser.claude.output_parser import EntityUpdate, RelationUpdate
from newsparser.claude.haiku import HAIKU_MODEL, ask_haiku
from newsparser.claude.runner import ClaudeError
from newsparser.graph.neo4j_client import get_driver
from newsparser.ignore import load_ignore

logger = logging.getLogger(__name__)

# Same alias classifier.py/tracker.py use.
_MAX_TOKENS = 2048

# All entity labels the resolver knows about — the full-text index and the
# top-N fallback span these.
_ENTITY_LABELS = (
    "Company", "Person", "Institution", "Event",
    "Indicator", "Market", "Sector", "Policy",
)
_ENTITY_INDEX = "entity_names"

_REGISTRY_LIMIT = 300  # fallback cap when candidate-targeted search is unavailable
_BASE_TOP_N = 100      # most-mentioned entities always folded into the registry
_FT_TOPK = 10          # per-candidate full-text hits
_EVENT_RECENT_DAYS = 14  # Events are time-local: recent ones re-appear, not top-mentioned
# Bound on the recent-Event bucket. last_seen is bumped to now() on every
# re-mention (and reset to now by a full graph replay), so "within 14 days" can
# collapse to *every* event and balloon the Haiku prompt — cap it at the most
# recent N. Full-text still covers name-similar events outside this window.
_EVENT_RECENT_LIMIT = 200
# Haiku 4.5 doesn't support adaptive thinking (this is plain generation
# latency — subprocess/CLI overhead plus a registry that can run to
# _REGISTRY_LIMIT entries), so a single 30s attempt isn't reliable once the
# registry grows. Retry a few times with backoff instead of failing the
# whole label group on one slow call.
_HAIKU_TIMEOUT = 60
_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds

_SYSTEM_PROMPT = (
    "You match candidate entity names against a registry of existing entities "
    "that may already represent the same real-world thing under a different "
    "name (language, abbreviation, ticker). Reply with exactly one line per "
    "candidate, format '<id>: <answer>', no explanations. <answer> is either "
    "a canonical_name copied EXACTLY (character-for-character) from the "
    "registry, or the literal word NEW."
)

_RESPONSE_LINE_RE = re.compile(r"^\s*([A-Za-z0-9]+)\s*:\s*(.+?)\s*$")


def _normalize(name: str) -> str:
    """Fold a name to a comparison key: NFKC + casefold, then drop everything
    that isn't a letter or digit (whitespace, punctuation). Makes NVIDIA≡Nvidia
    and 'SK hynix'≡'SK Hynix' collapse to one key while keeping CJK intact."""
    folded = unicodedata.normalize("NFKC", name or "").casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def _registry_norm_index(registry: list[dict]) -> dict[str, set[str]]:
    """Map each normalized surface form (canonical_name or alias) to the set of
    registry canonical_names that carry it. A key hitting >1 canonical_name is
    ambiguous — the deterministic pass refuses to confirm on it."""
    idx: dict[str, set[str]] = {}
    for r in registry:
        canon = r["name"]
        for surface in [canon, *(r.get("aliases") or [])]:
            key = _normalize(surface)
            if key:
                idx.setdefault(key, set()).add(canon)
    return idx


def _deterministic_matches(
    candidates: list[EntityUpdate], registry: list[dict]
) -> tuple[dict[str, tuple[str, str]], list[EntityUpdate]]:
    """Confirm renames without an LLM where the match is unambiguous AND the
    candidate's own *name* participates in it: the normalized name hits exactly
    one registry canonical name (directly or via that entry's aliases), with no
    second canonical name reachable through the candidate's aliases. Ambiguity
    is measured by distinct canonical name — the same name fragmented across
    labels counts as one match (we want to collapse it, not defer it), and the
    surviving label is the most-established one.

    A single hit reachable only through candidate *aliases* is NOT confirmed:
    aliases are LLM-extracted from article prose and one noisy shorthand alias
    ('삼성' on a Samsung Biologics candidate) must not silently merge two
    distinct entities. Those go to Haiku, which can answer NEW.

    Returns (rename, remaining):
      - rename: {candidate_name: (canonical_name, label)} for confirmed matches,
        including label-only overrides (same name, existing node's label wins).
      - remaining: candidates left for Haiku (no match, alias-only match, or an
        ambiguous multi-hit — a wrong merge is worse than a missed one).
    A candidate already equal to both the canonical name and its label resolves
    to itself: no rename, and dropped from the Haiku batch."""
    idx = _registry_norm_index(registry)
    label_of = _registry_label_map(registry)
    rename: dict[str, tuple[str, str]] = {}
    remaining: list[EntityUpdate] = []
    for c in candidates:
        name_key = _normalize(c.name)
        name_hits: set[str] = set(idx.get(name_key, set())) if name_key else set()
        alias_hits: set[str] = set()
        for surface in c.aliases:
            key = _normalize(surface)
            if key and key in idx:
                alias_hits |= idx[key]
        matches = name_hits | alias_hits
        if len(matches) == 1 and name_hits:
            canon = next(iter(matches))
            label = label_of.get(canon)
            if (canon, label) != (c.name, c.label):
                rename[c.name] = (canon, label)
            # else already canonical under the same label — skip Haiku
        else:
            # 0 matches → genuinely new (or long-tail); alias-only → suggestive
            # but not proof; >1 → ambiguous. All go to Haiku.
            remaining.append(c)
    return rename, remaining


# Company↔Institution is the only label pair whose boundary genuinely wobbles
# (a bank is both), so they share one resolution group and can cross-match.
# Every other label is its own group.
_LABEL_GROUPS: dict[str, str] = {"Company": "org", "Institution": "org"}


def _label_group(label: str) -> str:
    return _LABEL_GROUPS.get(label, label)


def _labels_in_group(group: str) -> list[str]:
    members = [lbl for lbl, grp in _LABEL_GROUPS.items() if grp == group]
    return members or [group]


_index_ensured = False

_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')
# Bare uppercase AND/OR/NOT are boolean operators to Lucene's query parser and
# raise ParseException in operator-illegal positions (e.g. a name starting with
# 'AND'). They can't be backslash-escaped; lowercasing makes them literal terms
# (the fulltext analyzer lowercases everything anyway, so matching is unchanged).
_LUCENE_OPERATORS = re.compile(r"\b(AND|OR|NOT)\b")


def _lucene_escape(text: str) -> str:
    """Escape Lucene query syntax so an arbitrary entity name is treated as
    literal terms, not an operator soup that raises a parse error."""
    escaped = _LUCENE_SPECIAL.sub(r"\\\1", text or "")
    return _LUCENE_OPERATORS.sub(lambda m: m.group(0).lower(), escaped)


def ensure_entity_index(session) -> bool:
    """Create the full-text index over entity names/aliases (idempotent) and
    wait until it is online. Returns False when the index can't be confirmed
    usable — the caller must treat the registry as incomplete (deterministic
    confirmation skipped, candidates routed to Haiku which can answer NEW),
    because a half-populated index makes a wrong match look unambiguous."""
    global _index_ensured
    if _index_ensured:
        return True
    labels = "|".join(_ENTITY_LABELS)
    stmt = (
        f"CREATE FULLTEXT INDEX {_ENTITY_INDEX} IF NOT EXISTS "
        f"FOR (n:{labels}) ON EACH [n.canonical_name, n.aliases]"
    )
    try:
        session.run(stmt)
        # A freshly created index populates asynchronously; block until ONLINE
        # (immediate no-op once populated) so the first cycles after deploy
        # don't resolve against a partial registry.
        session.run("CALL db.awaitIndex($name, $timeout)",
                    name=_ENTITY_INDEX, timeout=60)
        _index_ensured = True
        return True
    except Exception as exc:  # pragma: no cover - depends on live Neo4j
        logger.warning("entity full-text index unavailable (%s); "
                       "registry will be treated as incomplete", exc)
        return False


_REGISTRY_RETURN = (
    "e.canonical_name AS name, coalesce(e.aliases, []) AS aliases, "
    "[l IN labels(e) WHERE l IN $labels][0] AS label, "
    "coalesce(e.mention_count, 0) AS mention_count"
)


def _fetch_top_n(session, labels: list[str], limit: int) -> list[dict]:
    """Most-mentioned entities in the group — the always-present base registry
    (and the whole registry when candidate-targeted search is unavailable)."""
    result = session.run(
        "MATCH (e) WHERE any(l IN labels(e) WHERE l IN $labels) "
        f"RETURN {_REGISTRY_RETURN} "
        "ORDER BY e.mention_count DESC LIMIT $limit",
        labels=labels, limit=limit,
    )
    return [dict(r) for r in result]


def _fetch_full_text(
    session, labels: list[str], candidates: list[EntityUpdate]
) -> tuple[list[dict], bool]:
    """Per-candidate full-text hits — pulls long-tail entities the top-N base
    misses. Query terms are each candidate's name + aliases, Lucene-escaped.

    One statement per candidate (not a single UNWIND) so one unparsable name
    can't sink the whole batch. Returns (rows, ok); ok=False when any query
    failed — long-tail coverage is then incomplete and the caller must not
    treat the registry as complete."""
    queries = []
    for c in candidates:
        terms = " ".join(_lucene_escape(t) for t in [c.name, *c.aliases] if t and t.strip())
        if terms.strip():
            queries.append(terms)
    rows: list[dict] = []
    ok = True
    for q in queries:
        try:
            result = session.run(
                f"CALL db.index.fulltext.queryNodes('{_ENTITY_INDEX}', $q, {{limit: $k}}) "
                "  YIELD node AS e "
                "WITH DISTINCT e WHERE any(l IN labels(e) WHERE l IN $labels) "
                f"RETURN {_REGISTRY_RETURN}",
                q=q, k=_FT_TOPK, labels=labels,
            )
            rows.extend(dict(r) for r in result)
        except Exception as exc:
            ok = False
            logger.warning("full-text lookup failed for %r (%s)", q, exc)
    return rows, ok


def _fetch_recent_events(session, labels: list[str], days: int, limit: int) -> list[dict]:
    """Events are time-local — a re-appearing event is almost always a recent
    one, so fold recent Events into the registry rather than relying on
    mention_count (which buries fresh events in the long tail). Capped at the
    `limit` most-recent: last_seen bumps to now() on every re-mention/replay, so
    an uncapped window can degenerate to the entire Event set."""
    result = session.run(
        "MATCH (e:Event) WHERE e.last_seen >= datetime() - duration({days: $days}) "
        f"RETURN {_REGISTRY_RETURN} "
        "ORDER BY e.last_seen DESC LIMIT $limit",
        labels=labels, days=days, limit=limit,
    )
    return [dict(r) for r in result]


def fetch_registry(
    labels: list[str], candidates: list[EntityUpdate] | None = None
) -> tuple[list[dict], bool]:
    """Existing canonical_name/aliases/label/mention_count for entities in
    `labels`, deduped by canonical_name. Crossing a label group (e.g.
    Company+Institution) lets the resolver spot the same entity fragmented
    across labels; when two nodes share a canonical_name, the more-established
    (higher mention_count) row's label wins and the aliases are unioned so the
    other node's surface forms stay matchable.

    With `candidates`, builds a targeted registry (top-N base ∪ per-candidate
    full-text ∪ recent Events) instead of a flat top-N scan — this reaches
    long-tail entities the mention-count cap would hide.

    Returns (rows, complete). complete=False when the full-text layer could
    not fully cover the candidates (index missing/populating, query failures)
    — the caller must NOT run deterministic auto-merges against an incomplete
    registry, where a wrong match can look unambiguous; Haiku (which can
    answer NEW) is the only safe consumer then."""
    with get_driver().session() as session:
        if not candidates:
            # Legacy flat scan: no candidate-targeted coverage, so never
            # complete enough for deterministic confirmation.
            return _fetch_top_n(session, labels, _REGISTRY_LIMIT), False

        complete = ensure_entity_index(session)
        by_name: dict[str, dict] = {}

        def _fold(row: dict) -> None:
            cur = by_name.get(row["name"])
            if cur is None:
                by_name[row["name"]] = row
                return
            # Same canonical_name from two nodes (label fragmentation): keep
            # the more-established row, union aliases from the other.
            keep, other = ((row, cur)
                           if (row.get("mention_count") or 0) > (cur.get("mention_count") or 0)
                           else (cur, row))
            keep["aliases"] = keep["aliases"] + [
                a for a in other["aliases"] if a not in keep["aliases"]]
            by_name[row["name"]] = keep

        # Base is always present, so common entities stay covered even when
        # the full-text layer is degraded.
        for row in _fetch_top_n(session, labels, _BASE_TOP_N):
            _fold(row)
        ft_rows, ft_ok = _fetch_full_text(session, labels, candidates)
        complete = complete and ft_ok
        for row in ft_rows:
            _fold(row)
        if "Event" in labels:
            try:
                for row in _fetch_recent_events(session, labels, _EVENT_RECENT_DAYS, _EVENT_RECENT_LIMIT):
                    _fold(row)
            except Exception as exc:
                # Recent Events add name-divergent duplicates for Haiku's
                # benefit only — the deterministic pass can't match them —
                # so their loss doesn't make deterministic merges unsafe.
                logger.warning("recent-event registry lookup failed (%s)", exc)
        return list(by_name.values()), complete


def _build_prompt(registry: list[dict], candidates: list[EntityUpdate]) -> str:
    reg_lines = [
        f"{i + 1}. {r['name']} [{r.get('label', '?')}] | aliases: {', '.join(r['aliases'])}"
        for i, r in enumerate(registry)
    ]
    cand_lines = [
        f"C{i + 1}. {c.name} [{c.label}] | aliases: {', '.join(c.aliases)}"
        for i, c in enumerate(candidates)
    ]
    # Event names are cycle-authored prose (low reproducibility), so tilt the
    # judgment toward event identity rather than exact-name matching.
    event_hint = (
        "\n\nEvent는 같은 사건의 다른 서술일 가능성을 적극 의심하라: 주체가 겹치고 "
        "날짜가 ±2일 내이면 같은 실체로 보고 기존 canonical_name을 적어라."
        if any(c.label == "Event" for c in candidates) else ""
    )
    return (
        "기존 엔티티 목록 (label 표기):\n" + "\n".join(reg_lines) +
        "\n\n후보 (이번 사이클 신규 추출):\n" + "\n".join(cand_lines) +
        "\n\n각 후보가 기존 목록의 항목과 같은 실체를 가리키면 그 canonical_name을 "
        "정확히 그대로 적고, 새로운 실체면 NEW라고 적어. label이 달라도(예: Company vs "
        "Institution) 같은 실체일 수 있으니 이름으로 판단해라." + event_hint +
        "\n형식: 'C1: <canonical_name 또는 NEW>' 한 줄씩, 후보 전부에 대해."
    )


def _parse_response(
    raw: str, candidates: list[EntityUpdate], registry_names: set[str]
) -> dict[str, str]:
    by_id = {f"c{i + 1}": c.name for i, c in enumerate(candidates)}
    # Normalized fallback: casefold/spacing drift ("SK Hynix" vs the registry's
    # "SK hynix") shouldn't lose a match, but only when it resolves to a single
    # registry name — an ambiguous normalized collision is rejected.
    norm_to_names: dict[str, set[str]] = {}
    for rn in registry_names:
        norm_to_names.setdefault(_normalize(rn), set()).add(rn)
    rename: dict[str, str] = {}
    for line in (raw or "").splitlines():
        m = _RESPONSE_LINE_RE.match(line)
        if not m:
            continue
        cid, answer = m.group(1).lower(), m.group(2).strip()
        name = by_id.get(cid)
        if name is None or answer == "NEW":
            continue
        # Reject hallucinated names not in the registry — a wrong merge is worse
        # than a missed one. Accept an exact hit, else a unique normalized hit.
        # A resolution onto the candidate's own name IS returned: the caller
        # may still need it as a label-only override (same name, existing
        # node's label) and filters true no-ops itself.
        if answer in registry_names:
            resolved = answer
        else:
            hits = norm_to_names.get(_normalize(answer), set())
            resolved = next(iter(hits)) if len(hits) == 1 else None
        if resolved is not None:
            rename[name] = resolved
    return rename


def _run_claude_with_retry(prompt: str) -> str | None:
    """Retry the resolver's Haiku call a few times with backoff before giving
    up. Returns None (not raises) on exhaustion so the caller's existing
    fail-safe fallback (keep names as-is) still applies."""
    last_exc: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            return ask_haiku(prompt, _SYSTEM_PROMPT, _MAX_TOKENS, timeout=_HAIKU_TIMEOUT)
        except (ClaudeError, RuntimeError, OSError) as exc:
            last_exc = exc
            if attempt == _RETRIES - 1:
                break
            wait = _BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.25)
            logger.warning("haiku resolver attempt %d failed (%s); retrying in %.2fs",
                           attempt + 1, exc, wait)
            time.sleep(wait)
    logger.warning("haiku resolver gave up after %d attempts: %s", _RETRIES, last_exc)
    return None


def _registry_label_map(registry: list[dict]) -> dict[str, str]:
    """canonical_name → label of the most-established (highest mention_count)
    row carrying that name. Registry row order is NOT meaningful (top-N,
    full-text, and recent-Event buckets arrive in different orders), so the
    pick is by explicit mention_count — if the same name is fragmented across
    labels (the P1 duplicate itself), we stick to the established label
    rather than the cycle's fresh guess."""
    best: dict[str, tuple[int, str]] = {}
    for r in registry:
        mc = r.get("mention_count") or 0
        if r["name"] not in best or mc > best[r["name"]][0]:
            best[r["name"]] = (mc, r.get("label"))
    return {name: label for name, (_, label) in best.items()}


def resolve_entities(entities: list[EntityUpdate]) -> dict[str, tuple[str, str]]:
    """Return {original_name: (existing_canonical_name, existing_label)} for
    candidates matched to an existing entity within the same label group.
    Names absent from the map are left as-is (new entity, empty registry, or
    resolution failure). The label is the existing node's — applied so the
    write MERGEs onto that node instead of forking a new label.

    A name extracted under multiple label groups in the same batch is skipped
    entirely: the name-keyed rename map can't hold per-group targets, and
    relations reference endpoints by bare name — resolving one group's meaning
    would stomp the other's (missed merge over wrong merge)."""
    rename: dict[str, tuple[str, str]] = {}
    name_groups: dict[str, set[str]] = {}
    for e in entities:
        name_groups.setdefault(e.name, set()).add(_label_group(e.label))
    cross_group = {n for n, groups in name_groups.items() if len(groups) > 1}
    if cross_group:
        logger.warning("skipping resolution for name(s) extracted under multiple "
                       "label groups: %s", sorted(cross_group))

    by_group: dict[str, list[EntityUpdate]] = {}
    for e in entities:
        if e.name not in cross_group:
            by_group.setdefault(_label_group(e.label), []).append(e)

    for group, candidates in by_group.items():
        labels = _labels_in_group(group)
        try:
            registry, complete = fetch_registry(labels, candidates)
        except Exception as exc:
            logger.warning("registry fetch failed for group=%s (%s); skipping resolution", group, exc)
            continue
        if not registry:
            continue
        if complete:
            # Deterministic pass first: confirm unambiguous matches without an LLM
            # and shrink the Haiku batch (fewer/cheaper prompts, self-matches dropped).
            det_rename, remaining = _deterministic_matches(candidates, registry)
            rename.update(det_rename)
        else:
            # An incomplete registry can make a wrong match look unambiguous —
            # only Haiku (which can answer NEW) sees these candidates.
            logger.warning("registry for group=%s incomplete; skipping deterministic pass", group)
            remaining = candidates
        if remaining:
            prompt = _build_prompt(registry, remaining)
            raw = _run_claude_with_retry(prompt)
            if raw is None:
                logger.warning("entity resolution failed for group=%s after retries; keeping names as-is", group)
                continue
            label_of = _registry_label_map(registry)
            registry_names = {r["name"] for r in registry}
            cand_label = {c.name: c.label for c in remaining}
            for name, canon in _parse_response(raw, remaining, registry_names).items():
                label = label_of.get(canon)
                # Keep label-only overrides (same name, existing node's label
                # wins); drop true no-ops (same name AND same label).
                if (canon, label) != (name, cand_label.get(name)):
                    rename[name] = (canon, label)

    return rename


def prepare_graph_updates(
    entities: list[EntityUpdate],
    relations: list[RelationUpdate],
    workspace: Path,
) -> tuple[list[EntityUpdate], list[RelationUpdate]]:
    """Resolve entity names against the existing graph registry, then drop
    ignore-listed entities/relations. Shared by apply_graph.py (per-cycle)
    and restore_graph_from_cycles.py (full replay) so both take the same
    path onto the graph — a replay that skipped this would just reproduce
    whatever fragmentation already exists."""
    rename = resolve_entities(entities)
    if rename:
        for e in entities:
            if e.name in rename:
                new_name, new_label = rename[e.name]
                e.name = new_name
                if new_label:
                    e.label = new_label
        for r in relations:
            if r.subject in rename:
                r.subject = rename[r.subject][0]
            if r.obj in rename:
                r.obj = rename[r.obj][0]
        logger.info("entity resolver renamed %d candidate(s) to existing canonical names", len(rename))

    ignore = load_ignore(workspace)
    if ignore.entries:
        before_e, before_r = len(entities), len(relations)
        ignored = {e.name for e in entities
                   if ignore.matches_entity(e.name, e.aliases)}
        entities = [e for e in entities if e.name not in ignored]
        relations = [r for r in relations
                     if not (r.subject in ignored or r.obj in ignored
                             or ignore.matches_entity(r.subject, [])
                             or ignore.matches_entity(r.obj, []))]
        dropped_e, dropped_r = before_e - len(entities), before_r - len(relations)
        if dropped_e or dropped_r:
            logger.info("ignore filter dropped %d entities, %d relations",
                        dropped_e, dropped_r)

    return entities, relations
