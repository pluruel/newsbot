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


def test_matches_entity_ascii_target_respects_word_boundary(tmp_path):
    ws = _write_ignore(tmp_path, textwrap.dedent("""\
        | 종류 | 대상 | 추가일 | 메모 |
        |------|------|--------|------|
        | entity | AI | 2026-06-28 |  |
    """))
    ig = load_ignore(ws)
    # A short ASCII target must NOT over-match inside a longer word.
    assert ig.matches_entity("OpenAI", []) is False
    assert ig.matches_entity("xAI", []) is False
    assert ig.matches_entity("Air Liquide", []) is False
    # But it matches as a whole token.
    assert ig.matches_entity("Open AI", []) is True


def test_matches_ascii_target_not_substring_of_korean_word(tmp_path):
    ws = _write_ignore(tmp_path, textwrap.dedent("""\
        | 종류 | 대상 | 추가일 | 메모 |
        |------|------|--------|------|
        | entity | AI | 2026-06-28 |  |
    """))
    ig = load_ignore(ws)
    # "오픈AI" is one token; standalone-AI ignore must not drop it.
    assert ig.matches("오픈AI 신모델 공개") is False


def test_matches_entity_korean_target_keeps_substring(tmp_path):
    ws = _write_ignore(tmp_path, textwrap.dedent("""\
        | 종류 | 대상 | 추가일 | 메모 |
        |------|------|--------|------|
        | entity | 엔비디아 | 2026-06-28 |  |
    """))
    ig = load_ignore(ws)
    # Korean particles attach without a boundary → substring matching is kept.
    assert ig.matches_entity("엔비디아의 신규 칩", []) is True


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
