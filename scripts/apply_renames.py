"""Rename entity canonical_names in the graph from a JSON list.

  renames.json   [{"name","label","new_name"}]

Dry-run by default (`--apply` to mutate). Companion to scripts/merge_entities.py
and scripts/alias_cleanup.py — run this AFTER merges land (a rename may target a
node a merge just consolidated onto) and after alias cleanup (whose keys are the
pre-rename names).

Preserves the old canonical_name as an alias so the resolver still matches it,
and refuses to rename onto a name another node already holds (that would create
the very duplicate we're cleaning up). Labels are matched as a parameter
(`$label IN labels(e)`), never interpolated. Snapshot (`./backup.sh`) first.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from newsparser.graph.neo4j_client import get_driver

logger = logging.getLogger(__name__)


def _count(session, name: str, label: str) -> int:
    return session.run(
        "MATCH (e {canonical_name: $name}) WHERE $label IN labels(e) RETURN count(e) AS c",
        name=name, label=label,
    ).single()["c"]


def rename_node(session, entry: dict, apply: bool) -> dict:
    name, label, new = entry["name"], entry["label"], entry["new_name"]
    summary = {"from": f"{name} [{label}]", "to": new, "applied": False}
    if name == new:
        summary["skipped"] = "name unchanged"
        return summary
    if not _count(session, name, label):
        summary["skipped"] = "node not found"
        return summary
    if _count(session, new, label):
        summary["skipped"] = f"target name already exists [{label}] — would duplicate"
        return summary
    if not apply:
        summary["would_rename"] = True
        return summary
    session.run(
        "MATCH (e {canonical_name: $name}) WHERE $label IN labels(e) "
        "SET e.aliases = coalesce(e.aliases, []) + "
        "  [x IN [$name] WHERE NOT x IN coalesce(e.aliases, [])], "
        "  e.canonical_name = $new",
        name=name, label=label, new=new,
    )
    summary["applied"] = True
    return summary


def apply_renames(renames: list[dict], apply: bool) -> list[dict]:
    summaries: list[dict] = []
    with get_driver().session() as session:
        for entry in renames:
            try:
                summaries.append(rename_node(session, entry, apply))
            except (KeyError, TypeError) as exc:
                summaries.append({"entry": entry, "error": str(exc)})
    return summaries


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Rename entities from a JSON list.")
    parser.add_argument("renames_json", nargs="?", default="renames.json",
                        help="JSON list of {name, label, new_name} (default: renames.json)")
    parser.add_argument("--apply", action="store_true", help="Mutate the graph (default: dry-run).")
    args = parser.parse_args(argv)

    path = Path(args.renames_json)
    if not path.exists():
        print(f"Not found: {path}", file=sys.stderr)
        sys.exit(1)
    renames = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(renames, list):
        print("Input JSON must be a list.", file=sys.stderr)
        sys.exit(1)

    if args.apply:
        print("⚠️  --apply: this MUTATES the graph. Snapshot first:  ./backup.sh")
        print("   (run AFTER merge_entities.py and alias_cleanup.py)\n")
    else:
        print("dry-run (no changes). Re-run with --apply to execute.\n")

    for s in apply_renames(renames, apply=args.apply):
        print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
