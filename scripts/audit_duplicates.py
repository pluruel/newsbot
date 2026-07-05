"""Find near-duplicate entities already in the graph and propose merges.

Rule-based candidate extraction (no LLM needed to surface a candidate):
  (a) normalized surface collision within a label group — two nodes sharing a
      normalized canonical_name or alias (Company+Institution cross the group);
  (b) Event-only — shared subject token(s) AND event dates within ±2 days
      (Event names are cycle-authored prose, so exact match under-catches).

Each candidate pair is optionally confirmed by Haiku (same fail-safe as the
resolver: a failed/uncertain call leaves the pair *unconfirmed* — reported but
kept out of the auto-merge list). Direction: the higher mention_count node is
the `to` (survivor).

Outputs:
  - a human-readable report to stdout (every candidate + reason + verdict);
  - with --out, a JSON file in scripts/merge_entities.py's input format
    (confirmed pairs only, unless --no-llm).

Intended as a one-off cleanup after the resolver prevention (Phases 1-3) ships;
review the report by hand (especially name-divergent pairs like Citi/Citigroup)
before feeding the JSON to merge_entities.py --apply.

VERIFY on a graph copy first — the Cypher reads are only mock-tested here.
"""
import argparse
import json
import logging
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from newsparser.claude.runner import ClaudeError, run_claude
from newsparser.graph.neo4j_client import get_driver
from newsparser.graph.resolver import HAIKU_MODEL, _normalize

logger = logging.getLogger(__name__)

# Company+Institution cross-match (a bank is both); every other label is alone.
_ORG_GROUP = ["Company", "Institution"]
_SOLO_LABELS = ["Person", "Indicator", "Market", "Sector", "Policy"]
_EVENT_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_EVENT_DATE_DELTA = timedelta(days=2)
# Generic release/announce verbs carry no subject identity — two unrelated
# launches on adjacent dates ("Gemma 4 출시" vs "Claude Fable 5 출시") would
# otherwise pair on the shared verb alone. Excluded from subject-token overlap.
_EVENT_STOPWORDS = {_normalize(w) for w in (
    "출시", "발표", "공개", "배포", "발매", "릴리스", "오픈", "출간", "공식", "공표",
    "launch", "launches", "release", "released", "announce", "announced",
    "announcement", "unveil", "update",
)}

_CONFIRM_SYSTEM = (
    "You decide whether two graph entities are the same real-world thing "
    "(fragmented duplicates) or genuinely distinct. Reply one line per pair, "
    "'<id>: SAME' or '<id>: DIFF', no explanations. Default to DIFF when unsure."
)
_CONFIRM_LINE_RE = re.compile(r"^\s*P(\d+)\s*:\s*(SAME|DIFF)\s*$", re.IGNORECASE)
_CONFIRM_TIMEOUT = 300
_CONFIRM_RETRIES = 3
_CONFIRM_BACKOFF_BASE = 1.0  # seconds


# ---- pure rule logic (unit-tested) ----------------------------------------

def _surfaces(e: dict) -> set[str]:
    return {n for n in (_normalize(s) for s in [e["name"], *(e.get("aliases") or [])]) if n}


def _surface_collision_pairs(entities: list[dict]) -> list[tuple[int, int]]:
    """Index pairs whose normalized surface forms (name or alias) intersect."""
    surf_to_idx: dict[str, set[int]] = {}
    for i, e in enumerate(entities):
        for s in _surfaces(e):
            surf_to_idx.setdefault(s, set()).add(i)
    pairs: set[tuple[int, int]] = set()
    for idxs in surf_to_idx.values():
        ordered = sorted(idxs)
        for a in range(len(ordered)):
            for b in range(a + 1, len(ordered)):
                pairs.add((ordered[a], ordered[b]))
    return sorted(pairs)


def _parse_event_date(name: str):
    m = _EVENT_DATE_RE.search(name or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), "%Y-%m-%d").date()
    except ValueError:
        return None


def _event_tokens(name: str) -> set[str]:
    """Normalized subject tokens of an Event name: date, generic release verbs,
    and bare version/number tokens dropped — so overlap reflects the actual
    subject, not "released" or a shared version digit."""
    without_date = _EVENT_DATE_RE.sub(" ", name or "")
    toks = set()
    for raw in without_date.split():
        n = _normalize(raw)
        if n and n not in _EVENT_STOPWORDS and not n.isdigit():
            toks.add(n)
    return toks


def _event_pairs(events: list[dict]) -> list[tuple[int, int]]:
    """Index pairs of Events with overlapping subject tokens and dates ≤2d apart."""
    parsed = [(_parse_event_date(e["name"]), _event_tokens(e["name"])) for e in events]
    pairs: list[tuple[int, int]] = []
    for a in range(len(events)):
        da, ta = parsed[a]
        for b in range(a + 1, len(events)):
            db, tb = parsed[b]
            if da and db and abs(da - db) <= _EVENT_DATE_DELTA and (ta & tb):
                pairs.append((a, b))
    return pairs


def _directed(a: dict, b: dict) -> tuple[dict, dict]:
    """(from, to) — survivor (`to`) is the more-established (higher mention)."""
    if b.get("mention_count", 0) >= a.get("mention_count", 0):
        return a, b
    return b, a


def _to_merge_dict(from_e: dict, to_e: dict) -> dict:
    return {"from_name": from_e["name"], "from_label": from_e["label"],
            "to_name": to_e["name"], "to_label": to_e["label"]}


# ---- graph I/O + LLM confirmation -----------------------------------------

def fetch_entities(session, labels: list[str]) -> list[dict]:
    result = session.run(
        "MATCH (e) WHERE any(l IN labels(e) WHERE l IN $labels) "
        "RETURN e.canonical_name AS name, coalesce(e.aliases, []) AS aliases, "
        "  [l IN labels(e) WHERE l IN $labels][0] AS label, "
        "  coalesce(e.mention_count, 0) AS mention_count",
        labels=labels,
    )
    return [dict(r) for r in result]


def _confirm_pairs(candidates: list[dict]) -> dict[int, str] | None:
    """Ask Haiku which candidate pairs are the same entity — all pairs in one
    prompt, retried with backoff. Returns {index: 'SAME'|'DIFF'} for the pairs
    actually answered; pairs missing from the reply stay absent so the caller
    marks them 'unconfirmed' rather than silently 'rejected'. Returns None if
    the call still failed after retries (caller treats all as unconfirmed —
    fail-safe toward not merging)."""
    if not candidates:
        return {}
    lines = [
        f"P{i + 1}. {c['from']['name']} [{c['from']['label']}]  <->  "
        f"{c['to']['name']} [{c['to']['label']}]  (근거: {c['reason']})"
        for i, c in enumerate(candidates)
    ]
    prompt = (
        "다음 후보 쌍이 각각 같은 실체(분열된 중복)인지 판단해라:\n" + "\n".join(lines) +
        "\n\n각 쌍에 대해 'P1: SAME' 또는 'P1: DIFF' 한 줄씩. 확신 없으면 DIFF."
    )
    raw = None
    for attempt in range(_CONFIRM_RETRIES):
        try:
            raw = run_claude(prompt, timeout=_CONFIRM_TIMEOUT, model=HAIKU_MODEL,
                             system_prompt=_CONFIRM_SYSTEM, permission_mode="default")
            break
        except (ClaudeError, RuntimeError, OSError) as exc:
            if attempt == _CONFIRM_RETRIES - 1:
                logger.warning("pair confirmation failed after %d attempts (%s); "
                               "leaving all pairs unconfirmed", _CONFIRM_RETRIES, exc)
                return None
            wait = _CONFIRM_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.25)
            logger.warning("pair confirmation attempt %d failed (%s); retrying in %.2fs",
                           attempt + 1, exc, wait)
            time.sleep(wait)
    verdicts: dict[int, str] = {}
    for line in (raw or "").splitlines():
        m = _CONFIRM_LINE_RE.match(line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(candidates):
                verdicts[idx] = m.group(2).upper()
    return verdicts


def audit(session, use_llm: bool = True) -> list[dict]:
    """Return candidate records: {from, to, reason, verdict}. verdict is
    'confirmed' (Haiku answered SAME) | 'rejected' (Haiku answered DIFF) |
    'unconfirmed' (LLM skipped/failed, or the pair was missing from the
    reply — never judged, so never presented as judged-distinct)."""
    candidates: list[dict] = []
    seen: set[tuple] = set()

    def _add(entities, index_pairs, reason):
        for i, j in index_pairs:
            from_e, to_e = _directed(entities[i], entities[j])
            if (from_e["name"], from_e["label"]) == (to_e["name"], to_e["label"]):
                continue  # same node under two group labels in the fetch — nothing to merge
            key = (from_e["name"], from_e["label"], to_e["name"], to_e["label"])
            if key in seen:
                continue  # already surfaced by an earlier rule — one verdict per pair
            seen.add(key)
            candidates.append({"from": from_e, "to": to_e, "reason": reason})

    org = fetch_entities(session, _ORG_GROUP)
    _add(org, _surface_collision_pairs(org), "surface-collision (org group)")
    for label in _SOLO_LABELS:
        ents = fetch_entities(session, [label])
        _add(ents, _surface_collision_pairs(ents), f"surface-collision ({label})")
    events = fetch_entities(session, ["Event"])
    _add(events, _surface_collision_pairs(events), "surface-collision (Event)")
    _add(events, _event_pairs(events), "event subject+date (±2d)")

    answers = _confirm_pairs(candidates) if use_llm else None
    for i, c in enumerate(candidates):
        a = answers.get(i) if answers is not None else None
        c["verdict"] = ("confirmed" if a == "SAME"
                        else "rejected" if a == "DIFF"
                        else "unconfirmed")
    return candidates


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Audit the graph for duplicate entities.")
    parser.add_argument("--out", help="Write proposed merges (JSON) to this path.")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip Haiku confirmation; emit all rule candidates.")
    args = parser.parse_args(argv)

    with get_driver().session() as session:
        candidates = audit(session, use_llm=not args.no_llm)

    for c in candidates:
        print(f"[{c['verdict']:>11}] {c['from']['name']} [{c['from']['label']}] "
              f"-> {c['to']['name']} [{c['to']['label']}]  ({c['reason']})")
    print(f"\n{len(candidates)} candidate pair(s).", file=sys.stderr)

    if args.out:
        # Auto-merge list: confirmed pairs (or all, with --no-llm). Rejected pairs
        # never make the list; unconfirmed only when the LLM was skipped.
        emit = [c for c in candidates
                if c["verdict"] == "confirmed"
                or (args.no_llm and c["verdict"] == "unconfirmed")]
        merges = [_to_merge_dict(c["from"], c["to"]) for c in emit]
        Path(args.out).write_text(json.dumps(merges, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {len(merges)} proposed merge(s) to {args.out} — review before --apply.",
              file=sys.stderr)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
