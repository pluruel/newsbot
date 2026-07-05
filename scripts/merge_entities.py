"""Merge fragmented duplicate entities in the Neo4j graph.

Takes a JSON list of merge pairs and folds each `from` node into its `to` node:
relationships are rewritten onto `to` (direction + properties preserved, and
same-endpoint duplicates merged with writer.py's ON MATCH rules), aliases are
unioned (the `from` canonical_name becomes a `to` alias), mention_count is
summed, and the `from` node is deleted.

    [{"from_name": "Citi", "from_label": "Institution",
      "to_name": "Citigroup", "to_label": "Company"}, ...]

DESTRUCTIVE and hard to undo once relations combine into one node — so:
  - `--dry-run` is the DEFAULT; `--apply` is required to mutate.
  - snapshot the graph first (`./backup.sh`); the script reminds you.
  - compose has no APOC, so relationship moves are pure Cypher, per type.

Recommended origin: scripts/audit_duplicates.py emits exactly this JSON format
after a human reviews it. Run this AFTER the resolver prevention (Phases 1-3)
is deployed, so cleaned-up duplicates don't just re-accumulate.

VERIFY on a graph copy before touching prod: the Cypher is exercised only by
mocked unit tests here (this dev box has no Neo4j).
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from newsparser.graph.neo4j_client import get_driver

logger = logging.getLogger(__name__)

# Interpolated into Cypher (types/labels can't be parameterized), so gate hard.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Same weighted-blend + list-union rules writer.py uses on a relation ON MATCH,
# so a merged duplicate relation ends up identical to one that accreted normally.
_REL_ON_MATCH = (
    "  nr.impact_score = 0.85 * coalesce(nr.impact_score, r.impact_score) "
    "    + 0.15 * coalesce(r.impact_score, nr.impact_score), "
    "  nr.source_cycles = coalesce(nr.source_cycles, []) + coalesce(r.source_cycles, []), "
    "  nr.source_article_guids = coalesce(nr.source_article_guids, []) + "
    "    [g IN coalesce(r.source_article_guids, []) "
    "      WHERE NOT g IN coalesce(nr.source_article_guids, [])], "
    "  nr.category = coalesce(nr.category, r.category), "
    "  nr.confidence = coalesce(nr.confidence, r.confidence), "
    "  nr.first_seen = coalesce(nr.first_seen, r.first_seen), "
    "  nr.last_seen = coalesce(nr.last_seen, r.last_seen)"
)


def _validate_pair(pair: dict) -> tuple[str, str, str, str]:
    try:
        fn, fl = pair["from_name"], pair["from_label"]
        tn, tl = pair["to_name"], pair["to_label"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed pair (need from_name/from_label/to_name/to_label): {pair!r}") from exc
    if not _IDENT_RE.match(fl) or not _IDENT_RE.match(tl):
        raise ValueError(f"invalid label(s) in pair: {fl!r}, {tl!r}")
    if (fn, fl) == (tn, tl):
        raise ValueError(f"from and to are the same node: {pair!r}")
    return fn, fl, tn, tl


def _rel_types(session, fn: str, fl: str) -> list[str]:
    result = session.run(
        f"MATCH (f:{fl} {{canonical_name: $fn}})-[r]-() RETURN DISTINCT type(r) AS t",
        fn=fn,
    )
    types = [row["t"] for row in result]
    bad = [t for t in types if not _IDENT_RE.match(t or "")]
    if bad:
        raise ValueError(f"refusing to interpolate suspicious relationship type(s): {bad!r}")
    return types


def _move_relationships(session, fn, fl, tn, tl, rtype) -> None:
    # Outgoing: (f)-[r:T]->(x)  ⇒  (t)-[nr:T]->(x). Skip x==t (would be a self-loop).
    session.run(
        f"MATCH (f:{fl} {{canonical_name: $fn}})-[r:{rtype}]->(x) "
        f"MATCH (t:{tl} {{canonical_name: $tn}}) WHERE x <> t "
        f"MERGE (t)-[nr:{rtype}]->(x) "
        "ON CREATE SET nr = properties(r) "
        f"ON MATCH SET {_REL_ON_MATCH} "
        "DELETE r",
        fn=fn, tn=tn,
    )
    # Incoming: (y)-[r:T]->(f)  ⇒  (y)-[nr:T]->(t). Skip y==t.
    session.run(
        f"MATCH (y)-[r:{rtype}]->(f:{fl} {{canonical_name: $fn}}) "
        f"MATCH (t:{tl} {{canonical_name: $tn}}) WHERE y <> t "
        f"MERGE (y)-[nr:{rtype}]->(t) "
        "ON CREATE SET nr = properties(r) "
        f"ON MATCH SET {_REL_ON_MATCH} "
        "DELETE r",
        fn=fn, tn=tn,
    )


def _merge_node(session, fn, fl, tn, tl) -> None:
    # Union aliases (+ absorb from's canonical_name), sum mention_count, drop from.
    # Any relations left on f are the skipped self-loops — DETACH DELETE clears them.
    session.run(
        f"MATCH (f:{fl} {{canonical_name: $fn}}) "
        f"MATCH (t:{tl} {{canonical_name: $tn}}) "
        "SET t.aliases = coalesce(t.aliases, []) + "
        "  [a IN (coalesce(f.aliases, []) + [f.canonical_name]) "
        "    WHERE NOT a IN coalesce(t.aliases, [])], "
        "  t.mention_count = coalesce(t.mention_count, 0) + coalesce(f.mention_count, 0) "
        "DETACH DELETE f",
        fn=fn, tn=tn,
    )


def _describe(session, fn, fl, tn, tl) -> dict:
    """Read-only summary used by --dry-run (and to validate both nodes exist)."""
    row = session.run(
        f"OPTIONAL MATCH (f:{fl} {{canonical_name: $fn}}) "
        f"OPTIONAL MATCH (t:{tl} {{canonical_name: $tn}}) "
        "OPTIONAL MATCH (f)-[r]-() "
        "RETURN count(DISTINCT f) AS from_exists, count(DISTINCT t) AS to_exists, "
        "  count(r) AS rel_count",
        fn=fn, tn=tn,
    ).single()
    return dict(row) if row else {"from_exists": 0, "to_exists": 0, "rel_count": 0}


def merge_pair(session, pair: dict, apply: bool) -> dict:
    fn, fl, tn, tl = _validate_pair(pair)
    info = _describe(session, fn, fl, tn, tl)
    summary = {"from": f"{fn} [{fl}]", "to": f"{tn} [{tl}]",
               "rel_count": info["rel_count"], "applied": False}
    if not info["from_exists"]:
        summary["skipped"] = "from node not found"
        return summary
    if not info["to_exists"]:
        summary["skipped"] = "to node not found"
        return summary
    if not apply:
        summary["would_move_rels"] = info["rel_count"]
        return summary
    for rtype in _rel_types(session, fn, fl):
        _move_relationships(session, fn, fl, tn, tl, rtype)
    _merge_node(session, fn, fl, tn, tl)
    summary["applied"] = True
    return summary


def merge_all(pairs: list[dict], apply: bool) -> list[dict]:
    summaries = []
    with get_driver().session() as session:
        for pair in pairs:
            try:
                summaries.append(merge_pair(session, pair, apply))
            except ValueError as exc:
                logger.error("skipping pair: %s", exc)
                summaries.append({"pair": pair, "error": str(exc)})
    return summaries


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Merge duplicate graph entities from a JSON pair list.")
    parser.add_argument("pairs_json", help="Path to JSON list of {from_name, from_label, to_name, to_label}")
    parser.add_argument("--apply", action="store_true",
                        help="Actually mutate the graph (default: dry-run).")
    args = parser.parse_args(argv)

    pairs = json.loads(Path(args.pairs_json).read_text(encoding="utf-8"))
    if not isinstance(pairs, list):
        print("Input JSON must be a list of merge pairs.", file=sys.stderr)
        sys.exit(1)

    if args.apply:
        print("⚠️  --apply: this MUTATES the graph and is hard to undo.")
        print("   Snapshot first if you haven't:  ./backup.sh\n")
    else:
        print("dry-run (no changes). Re-run with --apply to execute.\n")

    for s in merge_all(pairs, apply=args.apply):
        print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
