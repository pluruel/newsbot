# newsparser/scripts/mark_processed.py
import os
import sys
from pathlib import Path

from newsparser._env_loader import load_env
load_env()

from newsparser.store.sqlite import mark_processed


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv
    if len(args) != 3:
        name = args[0] if args else "mark_processed.py"
        print(f"Usage: {name} <category> <slot>", file=sys.stderr)
        sys.exit(1)

    category, slot = args[1], args[2]
    workspace = Path(os.environ.get("WORKSPACE_DIR", "workspace"))
    guids_path = workspace / "input" / category / f"{slot}-guids.txt"

    if not guids_path.exists():
        print(f"Guids file not found: {guids_path}", file=sys.stderr)
        sys.exit(1)

    guids = [g for g in guids_path.read_text().splitlines() if g.strip()]
    if guids:
        mark_processed(guids)
    guids_path.unlink()
    print(f"Marked {len(guids)} articles as processed.")


if __name__ == "__main__":
    main()
