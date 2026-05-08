"""Re-apply graph updates from existing cycle reports.

Reads every workspace/cycles/{category}/*.md, parses its `## Graph updates`
section, and calls apply_graph_updates(). Use this to rebuild the Neo4j
graph from the durable markdown reports.

Note: last_seen/first_seen will be set to "now", not the original time.
"""
import argparse
import os
from pathlib import Path

from newsparser.claude.output_parser import parse_graph_updates
from newsparser.classifier import CATEGORIES
from newsparser.graph.writer import apply_graph_updates


def restore(workspace: Path) -> None:
    base = workspace / "cycles"
    for category in CATEGORIES:
        d = base / category
        if not d.exists():
            print(f"[skip] {d} does not exist")
            continue
        for f in sorted(d.glob("*.md")):
            report = f.read_text(encoding="utf-8")
            entities, relations = parse_graph_updates(report)
            cycle_id = f"{category}-{f.stem}"
            apply_graph_updates(entities, relations, cycle_id=cycle_id, category=category)
            print(f"[ok] {f}: {len(entities)} entities, {len(relations)} relations")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace",
        default=os.environ.get("WORKSPACE_DIR", "workspace"),
        help="Path to workspace dir (default: $WORKSPACE_DIR or ./workspace)",
    )
    args = parser.parse_args()
    restore(Path(args.workspace))


if __name__ == "__main__":
    main()
