import textwrap
from datetime import date

import pytest
from freezegun import freeze_time

from newsparser.ignore import (
    IgnoreEntry,
    IgnoreList,
    add_entry,
    load_ignore,
    format_list,
    remove_entry,
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


# --- writers ---------------------------------------------------------------

def test_add_entry_appends_row_and_keeps_prose(tmp_path):
    ws = _write_ignore(tmp_path, textwrap.dedent("""\
        # 무시 목록

        봇이 이 목록의 대상을 제외한다.

        | 종류 | 대상 | 추가일 | 메모 |
        |------|------|--------|------|
    """))
    add_entry("entity", "TSMC", "반복 노이즈", workspace=ws, today=date(2026, 8, 25))

    text = (ws / "me" / "ignore.md").read_text()
    assert "봇이 이 목록의 대상을 제외한다." in text      # prose survives
    assert "| entity | TSMC | 2026-08-25 | 반복 노이즈 |" in text
    assert [e.target for e in load_ignore(ws).entries] == ["TSMC"]


def test_add_entry_creates_table_when_file_has_none(tmp_path):
    ws = _write_ignore(tmp_path, "# 무시 목록\n")
    add_entry("storyline", "Opus 4.8 API 미등장", workspace=ws, today=date(2026, 8, 25))
    ig = load_ignore(ws)
    assert [(e.kind, e.target) for e in ig.entries] == [("storyline", "Opus 4.8 API 미등장")]


def test_add_entry_creates_file_when_missing(tmp_path):
    ws = tmp_path / "workspace"
    add_entry("entity", "TSMC", workspace=ws, today=date(2026, 8, 25))
    assert [e.target for e in load_ignore(ws).entries] == ["TSMC"]


@freeze_time("2026-08-24 20:00:00")   # 2026-08-25 05:00 KST
def test_add_entry_stamps_kst_date_not_utc(tmp_path):
    """The bot's user-facing dates are KST; a UTC stamp would render the entry
    a day early and make format_list show a negative age."""
    ws = _write_ignore(tmp_path, "| 종류 | 대상 | 추가일 | 메모 |\n|--|--|--|--|\n")
    entry = add_entry("entity", "TSMC", workspace=ws)
    assert entry.added == date(2026, 8, 25)


def test_add_entry_rejects_values_that_load_ignore_would_silently_skip(tmp_path):
    """An out-of-vocabulary 종류 makes load_ignore drop the row without a word,
    so the user hears "차단했다" while nothing filters. Reject it at write time."""
    ws = _write_ignore(tmp_path, "| 종류 | 대상 | 추가일 | 메모 |\n|--|--|--|--|\n")
    for kind, target in [("bogus", "X"), ("", "X"), ("entity", "  ")]:
        with pytest.raises(ValueError):
            add_entry(kind, target, workspace=ws)
    assert load_ignore(ws).entries == []


def test_add_entry_rejects_pipe_that_would_split_the_cell(tmp_path):
    ws = _write_ignore(tmp_path, "| 종류 | 대상 | 추가일 | 메모 |\n|--|--|--|--|\n")
    with pytest.raises(ValueError):
        add_entry("entity", "A | B", workspace=ws)


def test_add_entry_rejects_duplicate_casefolded(tmp_path):
    ws = _write_ignore(tmp_path, "| 종류 | 대상 | 추가일 | 메모 |\n|--|--|--|--|\n")
    add_entry("entity", "TSMC", workspace=ws, today=date(2026, 8, 25))
    with pytest.raises(ValueError):
        add_entry("entity", "tsmc", workspace=ws, today=date(2026, 8, 25))
    assert len(load_ignore(ws).entries) == 1


def test_remove_entry_drops_row_and_keeps_table_and_prose(tmp_path):
    ws = _write_ignore(tmp_path, textwrap.dedent("""\
        # 무시 목록

        설명 문단.

        | 종류 | 대상 | 추가일 | 메모 |
        |------|------|--------|------|
        | entity | TSMC | 2026-08-25 |  |
        | storyline | Opus 4.8 API 미등장 | 2026-08-25 |  |
    """))
    assert remove_entry("tsmc", workspace=ws) == 1          # casefold
    text = (ws / "me" / "ignore.md").read_text()
    assert "설명 문단." in text
    assert "| 종류 | 대상 | 추가일 | 메모 |" in text
    assert [e.target for e in load_ignore(ws).entries] == ["Opus 4.8 API 미등장"]


def test_remove_entry_no_match_returns_zero_and_leaves_file_untouched(tmp_path):
    body = "| 종류 | 대상 | 추가일 | 메모 |\n|--|--|--|--|\n| entity | TSMC | 2026-08-25 |  |\n"
    ws = _write_ignore(tmp_path, body)
    assert remove_entry("없는것", workspace=ws) == 0
    assert (ws / "me" / "ignore.md").read_text() == body


def test_remove_entry_missing_file_returns_zero(tmp_path):
    assert remove_entry("TSMC", workspace=tmp_path / "workspace") == 0
