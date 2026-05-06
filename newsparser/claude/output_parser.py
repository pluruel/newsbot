import re
from dataclasses import dataclass, field

ENTITY_RE = re.compile(
    r"^-\s+(NEW|UPDATE)\s+\|\s+(\w+)\s+\|\s+([^|]+?)"
    r"(?:\s+\|\s+aliases:\s+\[([^\]]*)\])?"
    r"(?:\s+\|\s+metadata:\s+.+)?$"
)
RELATION_RE = re.compile(
    r"^-\s+(NEW|UPDATE)\s+\|\s+(.+?)\s+--(\w+)"
    r"\[conf:([\d.]+),\s*impact:([\d.]+)\]-->\s+(.+?)"
    r"(?:\s+\|\s+(.+))?$"
)


@dataclass
class EntityUpdate:
    op: str
    label: str
    name: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class RelationUpdate:
    op: str
    subject: str
    predicate: str
    obj: str
    confidence: float
    impact_score: float
    predicate_text: str = ""


def parse_graph_updates(report: str) -> tuple[list[EntityUpdate], list[RelationUpdate]]:
    entities: list[EntityUpdate] = []
    relations: list[RelationUpdate] = []
    in_section = False

    for line in report.splitlines():
        stripped = line.strip()
        if stripped == "## Graph updates":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if not in_section or not stripped.startswith("-"):
            continue

        m = ENTITY_RE.match(stripped)
        if m:
            op, label, name, aliases_str = m.group(1), m.group(2), m.group(3).strip(), m.group(4)
            aliases = [a.strip() for a in (aliases_str or "").split(",") if a.strip()]
            entities.append(EntityUpdate(op=op, label=label, name=name, aliases=aliases))
            continue

        m = RELATION_RE.match(stripped)
        if m:
            op, subject, predicate, conf, impact, obj, text = m.groups()
            relations.append(RelationUpdate(
                op=op, subject=subject.strip(), predicate=predicate,
                obj=obj.strip(), confidence=float(conf), impact_score=float(impact),
                predicate_text=(text or "").strip(),
            ))

    return entities, relations
