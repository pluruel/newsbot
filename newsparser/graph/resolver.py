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
from newsparser.claude.runner import ClaudeError, run_claude
from newsparser.graph.neo4j_client import get_driver
from newsparser.ignore import load_ignore

logger = logging.getLogger(__name__)

# Same alias classifier.py/tracker.py use.
HAIKU_MODEL = "claude-haiku-4-5"

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
    """Confirm renames without an LLM where the match is unambiguous: a
    candidate's normalized name/alias hits exactly one registry *canonical
    name*. Ambiguity is measured by distinct canonical name — the same name
    fragmented across labels counts as one match (we want to collapse it, not
    defer it), and the surviving label is the most-established one.

    Returns (rename, remaining):
      - rename: {candidate_name: (canonical_name, label)} for confirmed matches,
        including label-only overrides (same name, existing node's label wins).
      - remaining: candidates left for Haiku (no match, or an ambiguous multi-hit
        onto genuinely different names — a wrong merge is worse than a missed one).
    A candidate already equal to both the canonical name and its label resolves
    to itself: no rename, and dropped from the Haiku batch."""
    idx = _registry_norm_index(registry)
    label_of = _registry_label_map(registry)
    rename: dict[str, tuple[str, str]] = {}
    remaining: list[EntityUpdate] = []
    for c in candidates:
        matches: set[str] = set()
        for surface in [c.name, *c.aliases]:
            key = _normalize(surface)
            if key and key in idx:
                matches |= idx[key]
        if len(matches) == 1:
            canon = next(iter(matches))
            label = label_of.get(canon)
            if (canon, label) != (c.name, c.label):
                rename[c.name] = (canon, label)
            # else already canonical under the same label — skip Haiku
        else:
            # 0 matches → genuinely new (or long-tail); >1 → ambiguous.
            # Both go to Haiku.
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


def _lucene_escape(text: str) -> str:
    """Escape Lucene query syntax so an arbitrary entity name is treated as
    literal terms, not an operator soup that raises a parse error."""
    return _LUCENE_SPECIAL.sub(r"\\\1", text or "")


def ensure_entity_index(session=None) -> None:
    """Create the full-text index over entity names/aliases, idempotently.
    Cheap after first run; safe to race (IF NOT EXISTS). Best-effort — a failure
    just leaves fetch_registry on its top-N fallback path."""
    global _index_ensured
    if _index_ensured:
        return
    labels = "|".join(_ENTITY_LABELS)
    stmt = (
        f"CREATE FULLTEXT INDEX {_ENTITY_INDEX} IF NOT EXISTS "
        f"FOR (n:{labels}) ON EACH [n.canonical_name, n.aliases]"
    )
    try:
        if session is not None:
            session.run(stmt)
        else:
            with get_driver().session() as s:
                s.run(stmt)
        _index_ensured = True
    except Exception as exc:  # pragma: no cover - depends on live Neo4j
        logger.warning("could not ensure entity full-text index (%s); "
                       "resolver will use top-N fallback", exc)


_REGISTRY_RETURN = (
    "e.canonical_name AS name, coalesce(e.aliases, []) AS aliases, "
    "[l IN labels(e) WHERE l IN $labels][0] AS label"
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


def _fetch_full_text(session, labels: list[str], candidates: list[EntityUpdate]) -> list[dict]:
    """Per-candidate full-text hits — pulls long-tail entities the top-N base
    misses. Query terms are each candidate's name + aliases, Lucene-escaped."""
    queries = []
    for c in candidates:
        terms = " ".join(_lucene_escape(t) for t in [c.name, *c.aliases] if t and t.strip())
        if terms.strip():
            queries.append(terms)
    if not queries:
        return []
    result = session.run(
        "UNWIND $queries AS q "
        f"CALL db.index.fulltext.queryNodes('{_ENTITY_INDEX}', q, {{limit: $k}}) "
        "  YIELD node AS e "
        "WITH DISTINCT e WHERE any(l IN labels(e) WHERE l IN $labels) "
        f"RETURN {_REGISTRY_RETURN}",
        queries=queries, k=_FT_TOPK, labels=labels,
    )
    return [dict(r) for r in result]


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


def fetch_registry(labels: list[str], candidates: list[EntityUpdate] | None = None) -> list[dict]:
    """Existing canonical_name/aliases/label for entities in `labels`, deduped
    by canonical_name. Crossing a label group (e.g. Company+Institution) lets
    the resolver spot the same entity fragmented across labels; the `label`
    column drives first-seen-sticky override.

    With `candidates`, builds a targeted registry (top-N base ∪ per-candidate
    full-text ∪ recent Events) instead of a flat top-300 — this reaches
    long-tail entities the mention-count cap would hide. Full-text failures
    (index missing/populating) degrade to just the top-N base; a total failure
    falls back to the legacy top-300 scan."""
    with get_driver().session() as session:
        if not candidates:
            return _fetch_top_n(session, labels, _REGISTRY_LIMIT)

        ensure_entity_index(session)
        by_name: dict[str, dict] = {}
        # Base is always present, so common entities stay covered even before
        # the full-text index finishes populating.
        for row in _fetch_top_n(session, labels, _BASE_TOP_N):
            by_name.setdefault(row["name"], row)
        try:
            for row in _fetch_full_text(session, labels, candidates):
                by_name.setdefault(row["name"], row)
        except Exception as exc:
            logger.warning("full-text registry lookup failed (%s); using top-N base only", exc)
        if "Event" in labels:
            try:
                for row in _fetch_recent_events(session, labels, _EVENT_RECENT_DAYS, _EVENT_RECENT_LIMIT):
                    by_name.setdefault(row["name"], row)
            except Exception as exc:
                logger.warning("recent-event registry lookup failed (%s)", exc)
        return list(by_name.values())


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
        if answer in registry_names:
            resolved = answer
        else:
            hits = norm_to_names.get(_normalize(answer), set())
            resolved = next(iter(hits)) if len(hits) == 1 else None
        if resolved is not None and resolved != name:
            rename[name] = resolved
    return rename


def _run_claude_with_retry(prompt: str) -> str | None:
    """Retry the resolver's Haiku call a few times with backoff before giving
    up. Returns None (not raises) on exhaustion so the caller's existing
    fail-safe fallback (keep names as-is) still applies."""
    last_exc: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            # Entity names are derived from scraped article content — run tool-less.
            return run_claude(prompt, timeout=_HAIKU_TIMEOUT, model=HAIKU_MODEL,
                               system_prompt=_SYSTEM_PROMPT, permission_mode="default")
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
    """canonical_name → label. Registry is ordered by mention_count desc, so the
    first occurrence of a name wins — if the same name is fragmented across
    labels (the P1 duplicate itself), we stick to the most-established label
    (first-seen-sticky approximation) rather than the cycle's fresh guess."""
    name_to_label: dict[str, str] = {}
    for r in registry:
        name_to_label.setdefault(r["name"], r.get("label"))
    return name_to_label


def resolve_entities(entities: list[EntityUpdate]) -> dict[str, tuple[str, str]]:
    """Return {original_name: (existing_canonical_name, existing_label)} for
    candidates matched to an existing entity within the same label group.
    Names absent from the map are left as-is (new entity, empty registry, or
    resolution failure). The label is the existing node's — applied so the
    write MERGEs onto that node instead of forking a new label."""
    rename: dict[str, tuple[str, str]] = {}
    by_group: dict[str, list[EntityUpdate]] = {}
    for e in entities:
        by_group.setdefault(_label_group(e.label), []).append(e)

    for group, candidates in by_group.items():
        labels = _labels_in_group(group)
        try:
            registry = fetch_registry(labels, candidates)
        except Exception as exc:
            logger.warning("registry fetch failed for group=%s (%s); skipping resolution", group, exc)
            continue
        if not registry:
            continue
        # Deterministic pass first: confirm unambiguous matches without an LLM
        # and shrink the Haiku batch (fewer/cheaper prompts, self-matches dropped).
        det_rename, remaining = _deterministic_matches(candidates, registry)
        rename.update(det_rename)
        if remaining:
            prompt = _build_prompt(registry, remaining)
            raw = _run_claude_with_retry(prompt)
            if raw is None:
                logger.warning("entity resolution failed for group=%s after retries; keeping names as-is", group)
                continue
            label_of = _registry_label_map(registry)
            registry_names = {r["name"] for r in registry}
            for name, canon in _parse_response(raw, remaining, registry_names).items():
                rename[name] = (canon, label_of.get(canon))

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
