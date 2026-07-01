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
import re

from newsparser.claude.output_parser import EntityUpdate
from newsparser.claude.runner import ClaudeError, run_claude
from newsparser.graph.neo4j_client import get_driver

logger = logging.getLogger(__name__)

# Same alias classifier.py/tracker.py use.
HAIKU_MODEL = "claude-haiku-4-5"

_REGISTRY_LIMIT = 300

_SYSTEM_PROMPT = (
    "You match candidate entity names against a registry of existing entities "
    "that may already represent the same real-world thing under a different "
    "name (language, abbreviation, ticker). Reply with exactly one line per "
    "candidate, format '<id>: <answer>', no explanations. <answer> is either "
    "a canonical_name copied EXACTLY (character-for-character) from the "
    "registry, or the literal word NEW."
)

_RESPONSE_LINE_RE = re.compile(r"^\s*([A-Za-z0-9]+)\s*:\s*(.+?)\s*$")


def fetch_registry(label: str) -> list[dict]:
    """Existing canonical_name/aliases for a label, ordered by mention_count desc."""
    with get_driver().session() as session:
        result = session.run(
            f"MATCH (e:{label}) RETURN e.canonical_name AS name, "
            "  coalesce(e.aliases, []) AS aliases "
            "ORDER BY e.mention_count DESC LIMIT $limit",
            limit=_REGISTRY_LIMIT,
        )
        return [dict(r) for r in result]


def _build_prompt(label: str, registry: list[dict], candidates: list[EntityUpdate]) -> str:
    reg_lines = [
        f"{i + 1}. {r['name']} | aliases: {', '.join(r['aliases'])}"
        for i, r in enumerate(registry)
    ]
    cand_lines = [
        f"C{i + 1}. {c.name} | aliases: {', '.join(c.aliases)}"
        for i, c in enumerate(candidates)
    ]
    return (
        f"기존 {label} 엔티티 목록:\n" + "\n".join(reg_lines) +
        f"\n\n후보 (이번 사이클 신규 추출, label={label}):\n" + "\n".join(cand_lines) +
        "\n\n각 후보가 기존 목록의 항목과 같은 실체를 가리키면 그 canonical_name을 "
        "정확히 그대로 적고, 새로운 실체면 NEW라고 적어. "
        "형식: 'C1: <canonical_name 또는 NEW>' 한 줄씩, 후보 전부에 대해."
    )


def _parse_response(
    raw: str, candidates: list[EntityUpdate], registry_names: set[str]
) -> dict[str, str]:
    by_id = {f"c{i + 1}": c.name for i, c in enumerate(candidates)}
    rename: dict[str, str] = {}
    for line in (raw or "").splitlines():
        m = _RESPONSE_LINE_RE.match(line)
        if not m:
            continue
        cid, answer = m.group(1).lower(), m.group(2).strip()
        name = by_id.get(cid)
        if name is None or answer == "NEW":
            continue
        # Reject hallucinated names not literally in the registry — a wrong
        # merge is worse than a missed one.
        if answer in registry_names and answer != name:
            rename[name] = answer
    return rename


def resolve_entities(entities: list[EntityUpdate]) -> dict[str, str]:
    """Return {original_name: existing_canonical_name} for candidates Haiku
    matched to an existing entity of the same label. Names absent from the
    map are left as-is (new entity, empty registry, or resolution failure)."""
    rename: dict[str, str] = {}
    by_label: dict[str, list[EntityUpdate]] = {}
    for e in entities:
        by_label.setdefault(e.label, []).append(e)

    for label, candidates in by_label.items():
        try:
            registry = fetch_registry(label)
        except Exception as exc:
            logger.warning("registry fetch failed for label=%s (%s); skipping resolution", label, exc)
            continue
        if not registry:
            continue
        prompt = _build_prompt(label, registry, candidates)
        try:
            raw = run_claude(prompt, timeout=30, model=HAIKU_MODEL, system_prompt=_SYSTEM_PROMPT)
        except (ClaudeError, RuntimeError, OSError) as exc:
            logger.warning("entity resolution failed for label=%s (%s); keeping names as-is", label, exc)
            continue
        registry_names = {r["name"] for r in registry}
        rename.update(_parse_response(raw, candidates, registry_names))

    return rename
