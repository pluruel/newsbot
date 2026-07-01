# newsparser/scripts/apply_graph.py
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from newsparser.claude.output_parser import parse_graph_updates
from newsparser.graph.resolver import resolve_entities
from newsparser.graph.writer import apply_graph_updates
from newsparser.market.annotate import maybe_annotate_impacts
from newsparser.ignore import load_ignore

logger = logging.getLogger(__name__)


def _resolve_source_indices(relations, guids: list[str]) -> None:
    """Mutate each relation's source_article_guids based on its source_indices."""
    for r in relations:
        resolved: list[str] = []
        for idx in r.source_indices:
            if not (len(idx) >= 2 and idx[0] == "A" and idx[1:].isdigit()):
                logger.warning("invalid src index %r — dropped", idx)
                continue
            n = int(idx[1:]) - 1
            if 0 <= n < len(guids):
                resolved.append(guids[n])
            else:
                logger.warning("out-of-range src index %r (have %d guids) — dropped",
                               idx, len(guids))
        r.source_article_guids = resolved


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

    rename = resolve_entities(entities)
    if rename:
        for e in entities:
            if e.name in rename:
                e.name = rename[e.name]
        for r in relations:
            if r.subject in rename:
                r.subject = rename[r.subject]
            if r.obj in rename:
                r.obj = rename[r.obj]
        logger.info("entity resolver renamed %d candidate(s) to existing canonical names", len(rename))

    guids_path = workspace / "input" / category / f"{slot}-guids.txt"
    guids = (guids_path.read_text().splitlines()
             if guids_path.exists() else [])
    guids = [g.strip() for g in guids if g.strip()]
    _resolve_source_indices(relations, guids)

    ignore = load_ignore(workspace)
    if ignore.entries:
        before_e, before_r = len(entities), len(relations)
        # Names of entities dropped by the ignore filter (incl. alias-only hits).
        # Relations are dropped if either endpoint is one of those names OR the
        # endpoint name itself matches a target — so an alias-matched entity
        # can't keep its relations (which reference it by canonical_name).
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

    cycle_id = f"{category}-{slot}"
    apply_graph_updates(entities, relations, cycle_id=cycle_id, category=category)
    print(f"Graph updated: {len(entities)} entities, {len(relations)} relations")

    try:
        annotated = maybe_annotate_impacts(relations, slot, category)
        if annotated:
            print(f"Annotated {annotated} relations with price reactions.")
    except Exception as exc:
        logger.warning("annotation pass failed: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    main()
