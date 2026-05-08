# newsparser/scripts/apply_graph.py
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from newsparser.claude.output_parser import parse_graph_updates
from newsparser.graph.writer import apply_graph_updates


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv
    if len(args) != 3:
        name = args[0] if args else "apply_graph.py"
        print(f"Usage: {name} <category> <slot>", file=sys.stderr)
        sys.exit(1)

    category, slot = args[1], args[2]
    workspace = Path(os.environ.get("WORKSPACE_DIR", "workspace"))
    report_path = workspace / "cycles" / category / f"{slot}.md"

    if not report_path.exists():
        print(f"Report not found: {report_path}", file=sys.stderr)
        sys.exit(1)

    report = report_path.read_text(encoding="utf-8")
    entities, relations = parse_graph_updates(report)
    cycle_id = f"{category}-{slot}"
    apply_graph_updates(entities, relations, cycle_id=cycle_id, category=category)
    print(f"Graph updated: {len(entities)} entities, {len(relations)} relations")


if __name__ == "__main__":
    main()
