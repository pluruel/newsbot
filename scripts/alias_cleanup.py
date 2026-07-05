"""Prune polluted aliases from graph entities, from a JSON list.

  alias_cleanup.json   [{"name","label","remove_aliases"}]

Removes specific aliases that a past cycle wrongly attached (e.g. a "SpaceX IPO"
alias on the distinct "SpaceX 나스닥 상장" node), which would otherwise let the
resolver re-merge entities we deliberately keep separate.

Dry-run by default (`--apply` to mutate). Companion to scripts/merge_entities.py
and scripts/apply_renames.py — run this BEFORE apply_renames.py so its keys still
match the pre-rename canonical_names. Labels are matched as a parameter
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


def remove_aliases(session, entry: dict, apply: bool) -> dict:
    name, label, rm = entry["name"], entry["label"], entry["remove_aliases"]
    summary = {"node": f"{name} [{label}]", "applied": False}
    row = session.run(
        "MATCH (e {canonical_name: $name}) WHERE $label IN labels(e) "
        "RETURN coalesce(e.aliases, []) AS a",
        name=name, label=label,
    ).single()
    if row is None:
        summary["skipped"] = "node not found"
        return summary
    present = [a for a in rm if a in row["a"]]
    summary["removing"] = present
    if not present:
        summary["skipped"] = "none of the listed aliases present"
        return summary
    if not apply:
        return summary
    session.run(
        "MATCH (e {canonical_name: $name}) WHERE $label IN labels(e) "
        "SET e.aliases = [a IN coalesce(e.aliases, []) WHERE NOT a IN $rm]",
        name=name, label=label, rm=rm,
    )
    summary["applied"] = True
    return summary


def apply_cleanups(cleanups: list[dict], apply: bool) -> list[dict]:
    summaries: list[dict] = []
    with get_driver().session() as session:
        for entry in cleanups:
            try:
                summaries.append(remove_aliases(session, entry, apply))
            except (KeyError, TypeError) as exc:
                summaries.append({"entry": entry, "error": str(exc)})
    return summaries


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Remove polluted aliases from a JSON list.")
    parser.add_argument("cleanup_json", nargs="?", default="alias_cleanup.json",
                        help="JSON list of {name, label, remove_aliases} (default: alias_cleanup.json)")
    parser.add_argument("--apply", action="store_true", help="Mutate the graph (default: dry-run).")
    args = parser.parse_args(argv)

    path = Path(args.cleanup_json)
    if not path.exists():
        print(f"Not found: {path}", file=sys.stderr)
        sys.exit(1)
    cleanups = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cleanups, list):
        print("Input JSON must be a list.", file=sys.stderr)
        sys.exit(1)

    if args.apply:
        print("⚠️  --apply: this MUTATES the graph. Snapshot first:  ./backup.sh")
        print("   (run BEFORE apply_renames.py so keys match pre-rename names)\n")
    else:
        print("dry-run (no changes). Re-run with --apply to execute.\n")

    for s in apply_cleanups(cleanups, apply=args.apply):
        print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
