# 사이클 텔레그램 메시지 축약 + 엔티티/서사 무시 목록 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 크론 사이클 텔레그램 메시지를 "중요도+제목"만 남기도록 Python에서 결정적으로 렌더하고, 외부에서 편집 가능한 엔티티/서사 무시 목록을 사이클·그래프·텔레그램 3지점에서 적용한다.

**Architecture:** 무시 목록은 `workspace/me/ignore.md` 마크다운 표(기존 manifesto/interests 패턴)에 저장하고 `newsparser/ignore.py`가 관용 파싱한다(DB 없음). 텔레그램 메시지는 LLM stdout이 아니라 저장된 리포트 .md에서 Python이 결정적으로 추출·정렬·필터한다. 무시 적용은 `cycle.md`(SOFT, 재서술 차단)·`apply_graph.py`(HARD, 그래프 유입 차단)·`run_cycle.py`(HARD, 텔레그램 노출 차단) 3지점.

**Tech Stack:** Python 3, pytest, freezegun, unittest.mock. Neo4j(필터만, 스키마 불변), Telegram(python-telegram-bot). Claude는 `claude -p` 헤드리스.

## Global Constraints

- 파이썬은 항상 `.venv/bin/python`, 테스트는 `.venv/bin/pytest`. `uv run` 금지.
- Claude는 헤드리스 `claude -p`만. Anthropic API 직접 호출 금지.
- 크론 사이클이 저장하는 파일(리포트 `.md`, input, guids, logs)과 Neo4j 스키마는 **형식·동작 불변**. DB 스키마/마이그레이션 변경 없음.
- 무시 목록의 유일한 신규 영속 데이터는 `workspace/me/ignore.md` 한 파일.
- 표 파서는 `newsparser/collector/sources.py` 패턴(관용·헤더 소문자 매핑)을 따른다.
- 날짜 계산("N일 경과")은 Python(`date.today()`)에서만. LLM 날짜 산술에 의존하지 않는다.
- `RelationUpdate`의 객체 필드명은 `.obj`(not `.object`), 주어는 `.subject`. `EntityUpdate`는 `.name`, `.aliases`.

---

### Task 1: `newsparser/ignore.py` — 무시 목록 로더

무시 목록의 파싱·매칭·목록 출력을 담당하는 기반 모듈. 이후 모든 태스크가 이걸 소비한다.

**Files:**
- Create: `newsparser/ignore.py`
- Test: `tests/test_ignore.py`

**Interfaces:**
- Produces:
  - `IgnoreEntry(kind: str, target: str, added: date | None = None, note: str = "")` — `kind`은 `"entity"` 또는 `"storyline"`.
  - `class IgnoreList` with `entries: list[IgnoreEntry]`, `entity_names() -> set[str]`(casefold), `storylines() -> list[str]`, `matches(text: str) -> bool`, `matches_entity(name: str, aliases: list[str]) -> bool`.
  - `load_ignore(workspace: Path | str | None = None) -> IgnoreList` — 파일 없으면 빈 목록.
  - `format_list(ignore: IgnoreList, today: date) -> str` — "N일 경과" 포함 사람용 목록.
  - `python -m newsparser.ignore` 실행 시 `format_list(load_ignore(), date.today())` 출력.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ignore.py`:

```python
import textwrap
from datetime import date

from freezegun import freeze_time

from newsparser.ignore import (
    IgnoreEntry,
    IgnoreList,
    load_ignore,
    format_list,
)


def _write_ignore(tmp_path, body: str):
    me = tmp_path / "workspace" / "me"
    me.mkdir(parents=True, exist_ok=True)
    (me / "ignore.md").write_text(body, encoding="utf-8")
    return tmp_path / "workspace"


def test_load_ignore_parses_entity_and_storyline_rows(tmp_path):
    ws = _write_ignore(tmp_path, textwrap.dedent("""\
        # 무시 목록

        | 종류 | 대상 | 추가일 | 메모 |
        |------|------|--------|------|
        | storyline | Opus 4.8 API 미등장 | 2026-06-01 | 잘못된 기록 |
        | entity | Claude Opus 4.8 | 2026-06-28 |  |
    """))
    ig = load_ignore(ws)
    assert len(ig.entries) == 2
    assert ig.entries[0].kind == "storyline"
    assert ig.entries[0].target == "Opus 4.8 API 미등장"
    assert ig.entries[0].added == date(2026, 6, 1)
    assert ig.entries[0].note == "잘못된 기록"
    assert ig.entries[1].kind == "entity"
    assert ig.entries[1].added == date(2026, 6, 28)


def test_accessors_split_by_kind(tmp_path):
    ws = _write_ignore(tmp_path, textwrap.dedent("""\
        | 종류 | 대상 | 추가일 | 메모 |
        |------|------|--------|------|
        | storyline | XX 인수설 | 2026-06-20 |  |
        | entity | Claude Opus 4.8 | 2026-06-28 |  |
    """))
    ig = load_ignore(ws)
    assert ig.entity_names() == {"claude opus 4.8"}
    assert ig.storylines() == ["XX 인수설"]


def test_matches_is_casefold_substring_over_all_targets(tmp_path):
    ws = _write_ignore(tmp_path, textwrap.dedent("""\
        | 종류 | 대상 | 추가일 | 메모 |
        |------|------|--------|------|
        | storyline | Opus 4.8 API 미등장 | 2026-06-01 |  |
        | entity | Claude Opus 4.8 | 2026-06-28 |  |
    """))
    ig = load_ignore(ws)
    assert ig.matches("오늘자 Opus 4.8 API 미등장 추측 재확산") is True
    assert ig.matches("CLAUDE OPUS 4.8 벤치마크 공개") is True   # casefold
    assert ig.matches("엔비디아 신규 GPU 공개") is False
    assert ig.matches("") is False


def test_matches_entity_checks_name_and_aliases(tmp_path):
    ws = _write_ignore(tmp_path, textwrap.dedent("""\
        | 종류 | 대상 | 추가일 | 메모 |
        |------|------|--------|------|
        | entity | Opus 4.8 | 2026-06-28 |  |
    """))
    ig = load_ignore(ws)
    assert ig.matches_entity("Claude Opus 4.8", []) is True       # substring in name
    assert ig.matches_entity("Some Model", ["opus 4.8 preview"]) is True  # substring in alias
    assert ig.matches_entity("OpenAI", []) is False


def test_load_ignore_missing_file_is_empty(tmp_path):
    ig = load_ignore(tmp_path / "workspace")
    assert ig.entries == []
    assert ig.matches("anything") is False
    assert ig.matches_entity("anything", []) is False


def test_load_ignore_skips_unknown_kind_and_blank_target(tmp_path):
    ws = _write_ignore(tmp_path, textwrap.dedent("""\
        | 종류 | 대상 | 추가일 | 메모 |
        |------|------|--------|------|
        | bogus | something | 2026-06-28 |  |
        | entity |  | 2026-06-28 | blank target |
        | entity | Real Entity | 2026-06-28 |  |
    """))
    ig = load_ignore(ws)
    assert len(ig.entries) == 1
    assert ig.entries[0].target == "Real Entity"


@freeze_time("2026-06-28")
def test_format_list_shows_days_since_added(tmp_path):
    ws = _write_ignore(tmp_path, textwrap.dedent("""\
        | 종류 | 대상 | 추가일 | 메모 |
        |------|------|--------|------|
        | storyline | Opus 4.8 API 미등장 | 2026-06-01 |  |
        | entity | Claude Opus 4.8 |  |  |
    """))
    ig = load_ignore(ws)
    out = format_list(ig, date.today())
    assert "무시 목록 (2건)" in out
    assert "[storyline] Opus 4.8 API 미등장 — 27일 경과" in out
    assert "[entity] Claude Opus 4.8 — 추가일 미상" in out


def test_format_list_empty(tmp_path):
    ig = IgnoreList([])
    assert format_list(ig, date(2026, 6, 28)) == "무시 목록이 비어 있음"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ignore.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'newsparser.ignore'`.

- [ ] **Step 3: Write the implementation**

Create `newsparser/ignore.py`:

```python
"""External ignore list: entity names and free-text storylines the bot should
exclude from cycle analysis, the graph, and Telegram.

Stored as a markdown table in ``workspace/me/ignore.md`` (human- and
bot-editable, same tier as manifesto/interests), parsed tolerantly like
``collector/sources.py``. No database — the file is the only persistent state.
"""
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

VALID_KINDS = ("entity", "storyline")


@dataclass
class IgnoreEntry:
    kind: str            # "entity" | "storyline"
    target: str
    added: date | None = None
    note: str = ""


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into stripped cells (sources.py convention)."""
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return all(set(c) <= set("-:") and c for c in cells)


def _parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s.strip())
    except (ValueError, AttributeError):
        return None


class IgnoreList:
    def __init__(self, entries: list[IgnoreEntry]):
        self.entries = entries

    def entity_names(self) -> set[str]:
        return {e.target.casefold() for e in self.entries
                if e.kind == "entity" and e.target}

    def storylines(self) -> list[str]:
        return [e.target for e in self.entries
                if e.kind == "storyline" and e.target]

    def _all_targets_cf(self) -> list[str]:
        return [e.target.casefold() for e in self.entries if e.target]

    def matches(self, text: str) -> bool:
        """True if any ignore target (entity or storyline) appears in ``text``
        as a casefold substring. Deterministic backstop for the Telegram render;
        semantic storyline exclusion is the cycle.md (SOFT) instruction's job."""
        if not text:
            return False
        t = text.casefold()
        return any(target in t for target in self._all_targets_cf())

    def matches_entity(self, name: str, aliases: list[str]) -> bool:
        """True if any entity-kind target is a casefold substring of the entity
        name or one of its aliases. Used to drop graph entities/relations."""
        targets = self.entity_names()
        if not targets:
            return False
        haystacks = [name.casefold()] + [a.casefold() for a in aliases]
        return any(target in h for target in targets for h in haystacks)


def _workspace(workspace: Path | str | None = None) -> Path:
    if workspace is not None:
        return Path(workspace)
    return Path(os.environ.get("WORKSPACE_DIR", "workspace"))


def load_ignore(workspace: Path | str | None = None) -> IgnoreList:
    path = _workspace(workspace) / "me" / "ignore.md"
    if not path.exists():
        return IgnoreList([])
    text = path.read_text(encoding="utf-8")

    header: list[str] | None = None
    entries: list[IgnoreEntry] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if header is None:
            header = [h.lower() for h in cells]
            continue
        if _is_separator(cells):
            continue
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        row = dict(zip(header, cells))

        kind = (row.get("종류") or row.get("kind") or "").strip().lower()
        target = (row.get("대상") or row.get("target") or "").strip()
        if kind not in VALID_KINDS or not target:
            continue
        entries.append(IgnoreEntry(
            kind=kind,
            target=target,
            added=_parse_date(row.get("추가일") or row.get("added") or ""),
            note=(row.get("메모") or row.get("note") or "").strip(),
        ))
    return IgnoreList(entries)


def format_list(ignore: IgnoreList, today: date) -> str:
    if not ignore.entries:
        return "무시 목록이 비어 있음"
    lines = [f"무시 목록 ({len(ignore.entries)}건)"]
    for e in ignore.entries:
        if e.added is None:
            age = "추가일 미상"
        else:
            age = f"{(today - e.added).days}일 경과"
        lines.append(f"• [{e.kind}] {e.target} — {age}")
    return "\n".join(lines)


def main() -> None:
    print(format_list(load_ignore(), date.today()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ignore.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Verify the CLI entrypoint**

Run: `WORKSPACE_DIR=/tmp/nonexistent .venv/bin/python -m newsparser.ignore`
Expected output: `무시 목록이 비어 있음`

- [ ] **Step 6: Commit**

```bash
git add newsparser/ignore.py tests/test_ignore.py
git commit -m "feat: add ignore-list loader (newsparser/ignore.py)"
```

---

### Task 2: `workspace.py` — `ignore.md` 시드

`ensure_workspace()`가 `manifesto.md`처럼 `ignore.md`를 "없으면 생성"한다.

**Files:**
- Modify: `newsparser/scheduler/workspace.py:24-28`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: Task 1의 `load_ignore`.
- Produces: 시드된 `workspace/me/ignore.md`(헤더만, 데이터 행 0개).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace.py`:

```python
def test_ensure_workspace_seeds_ignore_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    from newsparser.scheduler.workspace import ensure_workspace
    from newsparser.ignore import load_ignore

    root = ensure_workspace()
    ignore_path = root / "me" / "ignore.md"
    assert ignore_path.exists()
    # Seeded file is an empty (header-only) table.
    assert load_ignore(root).entries == []
    # Header row present so users/bot know the columns.
    assert "| 종류 | 대상 | 추가일 | 메모 |" in ignore_path.read_text()


def test_ensure_workspace_does_not_overwrite_existing_ignore(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    from newsparser.scheduler.workspace import ensure_workspace

    ensure_workspace()
    ignore_path = tmp_path / "workspace" / "me" / "ignore.md"
    ignore_path.write_text("CUSTOM CONTENT", encoding="utf-8")

    ensure_workspace()  # second call must not clobber
    assert ignore_path.read_text() == "CUSTOM CONTENT"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_workspace.py -k ignore -v`
Expected: FAIL — `ignore_path.exists()` is False (file not seeded yet).

- [ ] **Step 3: Implement the seed**

In `newsparser/scheduler/workspace.py`, after the manifesto block (line 24-26), add the ignore seed and a template function.

Replace:

```python
    manifesto = root / "me" / "manifesto.md"
    if not manifesto.exists():
        manifesto.write_text("", encoding="utf-8")

    return root
```

with:

```python
    manifesto = root / "me" / "manifesto.md"
    if not manifesto.exists():
        manifesto.write_text("", encoding="utf-8")

    ignore = root / "me" / "ignore.md"
    if not ignore.exists():
        ignore.write_text(_ignore_template(), encoding="utf-8")

    return root


def _ignore_template() -> str:
    return (
        "# 무시 목록 (ignore list)\n\n"
        "봇이 이 목록의 대상을 사이클 분석·다이제스트·그래프·텔레그램에서 제외한다.\n"
        '"무시: <대상>" 추가 · "무시 해제: <대상>" 삭제 · "차단 리스트" 조회.\n\n'
        "| 종류 | 대상 | 추가일 | 메모 |\n"
        "|------|------|--------|------|\n"
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_workspace.py -v`
Expected: PASS (all, including the two new ignore tests).

- [ ] **Step 5: Commit**

```bash
git add newsparser/scheduler/workspace.py tests/test_workspace.py
git commit -m "feat: seed workspace/me/ignore.md on workspace init"
```

---

### Task 3: `apply_graph.py` — 무시 엔티티/관계 필터 (HARD 그래프)

`apply_graph_updates` 호출 직전, 무시 엔티티명에 걸리는 `EntityUpdate`와 그 엔티티를 주어/목적어로 하는 `RelationUpdate`를 드롭한다. 기존 그래프 노드는 건드리지 않음(이후 사이클만).

**Files:**
- Modify: `newsparser/scripts/apply_graph.py:1-12, 56-59`
- Test: `tests/test_apply_graph.py`

**Interfaces:**
- Consumes: Task 1의 `load_ignore`, `IgnoreList.matches_entity`. `EntityUpdate.name`/`.aliases`, `RelationUpdate.subject`/`.obj`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_apply_graph.py`:

```python
SAMPLE_REPORT_WITH_IGNORED = """\
## Graph updates
### Entities
- NEW | Company | OpenAI | aliases: []
- NEW | Event | GPT-5 | aliases: []

### Relations
- NEW | OpenAI --ANNOUNCED[conf:0.9, impact:0.8]--> GPT-5 | announced new model
"""


def test_apply_graph_drops_ignored_entities_and_relations(tmp_path):
    ws = Path(os.environ["WORKSPACE_DIR"])
    (ws / "me").mkdir(parents=True, exist_ok=True)
    (ws / "me" / "ignore.md").write_text(
        "| 종류 | 대상 | 추가일 | 메모 |\n"
        "|------|------|--------|------|\n"
        "| entity | GPT-5 | 2026-06-28 |  |\n",
        encoding="utf-8",
    )
    (ws / "cycles" / "tech" / "2026-05-08-12.md").write_text(SAMPLE_REPORT_WITH_IGNORED)

    with patch("newsparser.scripts.apply_graph.apply_graph_updates") as mock_apply:
        script.main(["apply_graph.py", "tech", "2026-05-08-12"])

    entities, relations = mock_apply.call_args.args
    # Ignored entity and the relation referencing it are dropped.
    assert all(e.name != "GPT-5" for e in entities)
    assert relations == []
    # Non-ignored entity survives.
    assert any(e.name == "OpenAI" for e in entities)


def test_apply_graph_no_ignore_file_keeps_everything(tmp_path):
    ws = Path(os.environ["WORKSPACE_DIR"])
    (ws / "cycles" / "tech" / "2026-05-08-12.md").write_text(SAMPLE_REPORT_WITH_IGNORED)

    with patch("newsparser.scripts.apply_graph.apply_graph_updates") as mock_apply:
        script.main(["apply_graph.py", "tech", "2026-05-08-12"])

    entities, relations = mock_apply.call_args.args
    assert len(entities) == 2
    assert len(relations) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_apply_graph.py -k ignore -v`
Expected: FAIL — `test_apply_graph_drops_ignored_entities_and_relations` fails because GPT-5 entity/relation are still present (filter not implemented). (`test_apply_graph_no_ignore_file_keeps_everything` already passes.)

- [ ] **Step 3: Implement the filter**

In `newsparser/scripts/apply_graph.py`, add the import after line 11:

```python
from newsparser.market.annotate import maybe_annotate_impacts
from newsparser.ignore import load_ignore
```

Then, in `main()`, insert the filter between `_resolve_source_indices(relations, guids)` (line 56) and `cycle_id = ...` (line 58):

```python
    _resolve_source_indices(relations, guids)

    ignore = load_ignore(workspace)
    if ignore.entries:
        before_e, before_r = len(entities), len(relations)
        entities = [e for e in entities
                    if not ignore.matches_entity(e.name, e.aliases)]
        relations = [r for r in relations
                     if not (ignore.matches_entity(r.subject, [])
                             or ignore.matches_entity(r.obj, []))]
        dropped_e, dropped_r = before_e - len(entities), before_r - len(relations)
        if dropped_e or dropped_r:
            logger.info("ignore filter dropped %d entities, %d relations",
                        dropped_e, dropped_r)

    cycle_id = f"{category}-{slot}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_apply_graph.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 5: Commit**

```bash
git add newsparser/scripts/apply_graph.py tests/test_apply_graph.py
git commit -m "feat: filter ignored entities/relations before graph write"
```

---

### Task 4: `run_cycle.py` — 리포트에서 결정적 텔레그램 렌더 (Goal 1 + HARD 텔레그램)

LLM stdout·empty 폴백을 제거하고, 저장된 리포트 `.md`에서 `• (중요도 0.NN)` 항목만 추출 → 헤드라인 절단 → 무시 필터 → 중요도 내림차순 정렬 → `[CAT]\n• 0.NN 헤드라인` 평평한 목록 전송. **옛 동작을 검증하던 기존 테스트 2개를 갱신한다.**

**Files:**
- Modify: `newsparser/scripts/run_cycle.py:12-19(imports), 66, 78-91`
- Test: `tests/test_run_cycle_script.py` (기존 2개 갱신 + 신규 1개)

**Interfaces:**
- Consumes: Task 1의 `load_ignore`, `IgnoreList.matches`.
- Produces: 모듈 함수 `_render_telegram(report_text: str, ignore) -> list[str]` (테스트가 직접 호출하지 않아도 되지만 모듈에 존재).

- [ ] **Step 1: Update the existing tests + add the new one**

In `tests/test_run_cycle_script.py`:

(a) Replace `test_run_cycle_sends_digest_to_telegram` (lines 81-96) with:

```python
def test_run_cycle_sends_terse_importance_list(tmp_path):
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    sent: list[str] = []

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=_fake_run_claude_writes_report), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message", side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08-12")

    assert len(sent) == 1
    msg = sent[0]
    assert msg.startswith("[TECH]")
    assert "## Graph updates" not in msg
    assert "• 0.80 OpenAI 신모델 발표" in msg   # rendered from report file, score formatted 0.NN
    assert "(중요도" not in msg                   # verbose file markup is NOT sent
```

(b) Replace `test_run_cycle_falls_back_to_file_digest_when_stdout_empty` (lines 99-125) with:

```python
def test_run_cycle_renders_from_file_ignoring_stdout(tmp_path):
    """Telegram is rendered from the report file; LLM stdout is irrelevant."""
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    sent: list[str] = []

    def fake_garbage_stdout(prompt, **kw):
        import os as _os
        from pathlib import Path as _Path
        ws = _Path(_os.environ["WORKSPACE_DIR"])
        slot, category = prompt.strip().split()[1:3]
        report_dir = ws / "cycles" / category
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{slot}.md").write_text(SAMPLE_REPORT)
        return "ASDF 쓰레기 stdout 무시되어야 함"  # must be ignored

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_garbage_stdout), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message", side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08-12")

    assert len(sent) == 1
    msg = sent[0]
    assert msg.startswith("[TECH]")
    assert "• 0.80 OpenAI 신모델 발표" in msg
    assert "쓰레기 stdout" not in msg


def test_run_cycle_drops_ignored_headline_from_telegram(tmp_path):
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    two_item_report = (
        "사이클 2026-05-08 12:00 KST\n\n"
        "새 소식\n"
        "• (중요도 0.9) Claude Opus 4.8 API 미등장 추측 재확산. 본문.\n"
        "• (중요도 0.7) 엔비디아 신규 GPU 공개. 본문.\n\n"
        "## Graph updates\n"
    )

    def fake_claude(prompt, **kw):
        import os as _os
        from pathlib import Path as _Path
        ws = _Path(_os.environ["WORKSPACE_DIR"])
        slot, category = prompt.strip().split()[1:3]
        (ws / "cycles" / category).mkdir(parents=True, exist_ok=True)
        (ws / "cycles" / category / f"{slot}.md").write_text(two_item_report)
        (ws / "me").mkdir(parents=True, exist_ok=True)
        (ws / "me" / "ignore.md").write_text(
            "| 종류 | 대상 | 추가일 | 메모 |\n"
            "|------|------|--------|------|\n"
            "| storyline | Opus 4.8 API 미등장 | 2026-06-28 |  |\n",
            encoding="utf-8",
        )
        return ""

    sent: list[str] = []
    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_claude), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message", side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08-12")

    assert len(sent) == 1
    msg = sent[0]
    assert "미등장" not in msg                       # ignored storyline dropped
    assert "• 0.70 엔비디아 신규 GPU 공개" in msg     # other item kept
```

Note: `SAMPLE_REPORT` (lines 18-32) already has `• (중요도 0.8) OpenAI 신모델 발표.` — keep it as-is. The unused `TERSE_SUMMARY` constant (lines 37-40) may remain; it is harmless.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_run_cycle_script.py -v`
Expected: FAIL — the three render tests fail (`_render_telegram` not implemented; current code sends stdout/full digest, not `• 0.80 ...`).

- [ ] **Step 3: Implement the render**

In `newsparser/scripts/run_cycle.py`:

(a) Add `import re` near the top imports, and add the ignore import after the existing `from newsparser.scheduler.workspace import ensure_workspace` (line 19):

```python
import re
```
```python
from newsparser.scheduler.workspace import ensure_workspace
from newsparser.ignore import load_ignore
```

(b) Add the regex + helper at module level (after `_KST = ZoneInfo("Asia/Seoul")`, line 23):

```python
_CYCLE_ITEM_RE = re.compile(r"^\s*[•\-\*]\s*\(중요도\s*([0-9]*\.?[0-9]+)\)\s*(.+)$")


def _render_telegram(report_text: str, ignore) -> list[str]:
    """Build the terse Telegram lines from a saved cycle report.

    Extract `• (중요도 0.NN) 헤드라인. 본문…` items from the digest (everything
    before `## Graph updates`), keep only the headline (text before the first
    sentence end), drop ignored ones, and return `• 0.NN 헤드라인` sorted by
    importance descending. Items without a 중요도 score (조용한 영역 / 오픈 스레드)
    are naturally excluded.
    """
    digest = report_text.split("## Graph updates", 1)[0]
    items: list[tuple[float, str]] = []
    seen: set[str] = set()
    for line in digest.splitlines():
        m = _CYCLE_ITEM_RE.match(line)
        if not m:
            continue
        score = float(m.group(1))
        headline = m.group(2).split(". ", 1)[0].rstrip(". ").strip()
        if not headline or headline in seen:
            continue
        if ignore.matches(headline):
            continue
        seen.add(headline)
        items.append((score, headline))
    items.sort(key=lambda x: x[0], reverse=True)
    return [f"• {score:.2f} {headline}" for score, headline in items]
```

(c) In `_run_for_category`, change line 66 to drop the unused assignment:

```python
    run_claude(f"/cycle {slot} {category}")
```

(d) Replace the Telegram block (lines 78-91, the comment + `report_path` + `summary` logic + fallback + send) with:

```python
    # Telegram gets a terse, importance-sorted list rendered deterministically
    # from the saved report file (NOT the LLM stdout), with ignored
    # entities/storylines dropped. The full digest stays in the report file.
    report_path = workspace / "cycles" / category / f"{slot}.md"
    if report_path.exists():
        ignore = load_ignore(workspace)
        lines = _render_telegram(report_path.read_text(encoding="utf-8"), ignore)
        body = "\n".join(lines) if lines else "새 소식 없음"
        try:
            send_long_message(f"[{category.upper()}]\n{body}")
        except Exception as e:
            logger.error("Telegram send failed for %s/%s: %s", category, slot, e)
    else:
        logger.warning("[%s] no report file at %s — skipping telegram",
                       category, report_path)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_run_cycle_script.py -v`
Expected: PASS (all, including the three render tests and the unchanged `test_run_cycle_skips_empty_category`, `test_run_cycle_category_error_doesnt_stop_other`, `test_snapshot_block_prepended_to_input`).

- [ ] **Step 5: Commit**

```bash
git add newsparser/scripts/run_cycle.py tests/test_run_cycle_script.py
git commit -m "feat: render terse importance-sorted telegram message from report file"
```

---

### Task 5: `.claude/commands/cycle.md` — SOFT 무시 + stdout 요약 제거

사이클 프롬프트가 (a) 무시 목록을 읽어 직전 리포트 재서술·점수·그래프에서 제외하고, (b) 더는 stdout 텔레그램 요약을 출력하지 않도록 한다. **리포트 형식(38-65)·step 1·3–6·문체 규칙은 불변.**

**Files:**
- Modify: `.claude/commands/cycle.md:9-11(관심사 근처), 20(step 2), 30(step 7), 72-91(stdout 섹션)`

**Interfaces:** 런타임 프롬프트 변경 — 자동 테스트 없음. 검증은 파일 정독 + grep.

- [ ] **Step 1: Add the SOFT ignore instruction to "사용자 관심사" area**

After the "사용자 관심사" block (line 9-11), add a new section:

```markdown
## 무시 목록

Read `workspace/me/ignore.md`. 표의 모든 `대상`(종류 entity/storyline)을 이번 사이클에서 **완전히 배제**한다:
- 직전 사이클 리포트(아래 step 2)에서 해당 화제를 **이어받아 재서술하지 않는다.**
- 중요도 점수·다이제스트 본문·`## Graph updates` 블록 어디에도 포함하지 않는다.
- 목록이 비어 있으면 무시.
```

- [ ] **Step 2: Reference the ignore list in step 2**

Replace step 2 (line 20):

```markdown
2. Read the most recent file in `workspace/cycles/{category}/` for prior context (skip if none exist).
```

with:

```markdown
2. Read the most recent file in `workspace/cycles/{category}/` for prior context (skip if none exist). **무시 목록의 대상이 직전 리포트에 등장하더라도 이어받지 말 것** (위 "무시 목록" 참조).
```

- [ ] **Step 3: Simplify step 7 (no more stdout telegram summary)**

Replace step 7 (line 30):

```markdown
7. 마지막으로 텔레그램 전송용 키워드 요약을 **stdout(최종 메시지)** 으로 출력한다. 형식은 아래 "텔레그램 전송용 요약 (stdout)"을 따른다. 이것이 텔레그램으로 전송되는 유일한 출력이며, 리포트 파일에는 넣지 않는다.
```

with:

```markdown
7. 텔레그램 메시지는 이제 Python(`run_cycle.py`)이 리포트 파일에서 직접 렌더하므로, **별도의 stdout 요약을 출력할 필요가 없다.** 리포트 `.md`만 위 형식대로 정확히 작성하면 된다 (특히 각 항목의 `• (중요도 0.NN) 헤드라인`을 정확한 형식으로).
```

- [ ] **Step 4: Remove the stdout summary spec section**

Delete the entire "텔레그램 전송용 요약 (stdout)" section (lines 72-91, from `## 텔레그램 전송용 요약 (stdout)` through the closing ` ``` ` of its example block).

- [ ] **Step 5: Verify the edits**

Run: `grep -n "텔레그램 전송용 요약" .claude/commands/cycle.md`
Expected: no output (section removed).

Run: `grep -n "무시 목록" .claude/commands/cycle.md`
Expected: two matches (the new section header + the step 2 reference).

Read the file top-to-bottom and confirm the report format block (`## Report file format`, Valid Labels/Predicates) is intact and unchanged.

- [ ] **Step 6: Commit**

```bash
git add .claude/commands/cycle.md
git commit -m "feat: cycle prompt reads ignore list, drops stdout telegram summary"
```

---

### Task 6: `tracker.py` — 텔레그램 무시 명령 + admin 마커

tracker 봇이 "무시:" / "무시 해제:" / "차단 리스트" 자유문구를 처리하도록 프롬프트 힌트를 추가하고, 무시 편집이 대화 히스토리를 오염시키지 않도록 admin 마커를 등록한다.

**Files:**
- Modify: `newsparser/bot/tracker.py:106-133(prompt), 142-148(_ADMIN_MARKERS)`
- Test: `tests/test_tracker.py`

**Interfaces:**
- Consumes: 기존 tracker의 `Read/Edit/Write` + `bypassPermissions`, Task 1의 `python -m newsparser.ignore` CLI.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tracker.py`:

```python
def test_ignore_marker_registered():
    from newsparser.bot.tracker import _ADMIN_MARKERS
    assert "ignore.md updated" in _ADMIN_MARKERS


def test_ignore_marker_skips_history_save(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    import newsparser.bot.tracker as tracker

    monkeypatch.setattr(tracker, "run_claude",
                        lambda *a, **k: "추가했습니다. ignore.md updated")
    monkeypatch.setattr(tracker, "classify_query", lambda *a, **k: "both")

    tracker.run_tracker("chat-xyz", "무시: Opus 4.8 API 미등장")

    # admin marker present → conversation history must NOT be saved
    assert tracker.load_history("chat-xyz") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tracker.py -k ignore -v`
Expected: FAIL — `"ignore.md updated"` not in `_ADMIN_MARKERS`.

- [ ] **Step 3: Add the admin marker**

In `newsparser/bot/tracker.py`, extend `_ADMIN_MARKERS` (lines 142-148):

```python
    _ADMIN_MARKERS = (
        "interests_tech.md updated",
        "interests_markets.md updated",
        "manifesto.md updated",
        "ignore.md updated",
        "cleared",
        "interest-events.jsonl",
    )
```

- [ ] **Step 4: Add the prompt hint**

In the prompt string in `run_tracker`, insert a hint block before the final formatting instruction (i.e. before the `"Answer in plain conversational paragraphs"` line, around line 128):

```python
        "무시 목록 관리 권한도 있다. 사용자가 특정 엔티티/서사를 더는 다루지 말라고 하면"
        "(\"무시: X\", \"X 무시해\"), `workspace/me/ignore.md` 표에 행을 추가한다. "
        "단일 엔티티명이면 종류=entity, 서사·주장 문구면 종류=storyline, 추가일은 오늘(YYYY-MM-DD). "
        "\"무시 해제: X\"면 해당 행을 삭제한다. 이렇게 ignore.md를 편집한 경우 답변에 "
        "정확히 `ignore.md updated` 문구를 포함한다. "
        "\"차단 리스트\"/\"무시 목록 보여줘\"면 `.venv/bin/python -m newsparser.ignore`를 "
        "Bash로 실행해 그 출력(대상 + N일 경과)을 그대로 사용자에게 전달한다.\n\n"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_tracker.py -v`
Expected: PASS (all, including the two new ignore tests).

- [ ] **Step 6: Commit**

```bash
git add newsparser/bot/tracker.py tests/test_tracker.py
git commit -m "feat: telegram ignore commands (add/remove/list) via tracker"
```

---

### Task 7: 전체 회귀 검증

**Files:** 없음 (검증만).

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS, 0 failed. 특히 회귀 위험 파일: `test_run_cycle_script.py`, `test_apply_graph.py`, `test_workspace.py`, `test_tracker.py`, `test_output_parser.py`.

- [ ] **Step 2: Manual smoke of the ignore CLI**

```bash
WS=$(mktemp -d)/ws
mkdir -p "$WS/me"
printf '| 종류 | 대상 | 추가일 | 메모 |\n|------|------|--------|------|\n| storyline | Opus 4.8 API 미등장 | 2026-06-01 |  |\n' > "$WS/me/ignore.md"
WORKSPACE_DIR="$WS" .venv/bin/python -m newsparser.ignore
```

Expected: prints
```
무시 목록 (1건)
• [storyline] Opus 4.8 API 미등장 — <N>일 경과
```
(N = days between 2026-06-01 and today.)

- [ ] **Step 3: No commit** (검증 태스크).

---

## 변경 파일 요약

| 파일 | 태스크 | 변경 |
|------|:--:|------|
| `newsparser/ignore.py` | 1 | 신규 — 로더/매칭/목록 CLI |
| `tests/test_ignore.py` | 1 | 신규 |
| `newsparser/scheduler/workspace.py` | 2 | `ignore.md` 시드 |
| `tests/test_workspace.py` | 2 | 시드 테스트 |
| `newsparser/scripts/apply_graph.py` | 3 | 무시 엔티티/관계 필터 |
| `tests/test_apply_graph.py` | 3 | 필터 테스트 |
| `newsparser/scripts/run_cycle.py` | 4 | 리포트→텔레그램 결정적 렌더 + 무시 필터 |
| `tests/test_run_cycle_script.py` | 4 | 기존 2개 갱신 + 신규 1개 |
| `.claude/commands/cycle.md` | 5 | SOFT 무시 + stdout 요약 제거 |
| `newsparser/bot/tracker.py` | 6 | 무시 명령 힌트 + admin 마커 |
| `tests/test_tracker.py` | 6 | 마커/히스토리 테스트 |

## Self-Review 메모 (작성자 확인 완료)

- **Spec 커버리지**: G1(메시지)→Task 4·5, G2(무시 적용 3지점)→Task 3·4·5, G3(텔레그램 명령·경과일)→Task 1·6. 저장 파일 불변/DB 불변(비목표)→ 어떤 태스크도 SQLite/Neo4j 스키마·리포트 형식을 수정하지 않음.
- **타입 일관성**: `RelationUpdate.obj`(not `.object`), `.subject`; `EntityUpdate.name/.aliases`; `parse_graph_updates -> (entities, relations)`; `send_long_message(text)`; `load_ignore(workspace)`/`IgnoreList.matches`/`.matches_entity`/`format_list(ignore, today)` 전 태스크 동일.
- **회귀**: `test_run_cycle_script.py`의 옛 동작 검증 2개를 Task 4 Step 1에서 명시적으로 교체.
- **storyline 매칭 한계**: `matches()`는 casefold 부분일치(결정적 백스톱). 서사의 의미적 배제는 Task 5의 SOFT cycle.md 지시가 주력 — 두 경로 병행이 의도된 설계.
