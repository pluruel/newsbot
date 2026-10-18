# tests/test_run_reflect.py
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from newsparser import triage
from newsparser.claude.runner import ClaudeError
import newsparser.scripts.run_reflect as script

_OLD_TECH = "| 테마 | interest_weight |\n|---|---|\n| OLD-TECH | 0.5 |\n"
_OLD_MARKETS = "| 테마 | interest_weight |\n|---|---|\n| OLD-MARKETS | 0.5 |\n"
_NEW_TECH = "| 테마 | interest_weight |\n|---|---|\n| NEW-TECH | 0.8 |\n"
_NEW_MARKETS = "| 테마 | interest_weight |\n|---|---|\n| NEW-MARKETS | 0.8 |\n"


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("NEO4J_PASSWORD", "testpass")
    monkeypatch.setenv("CONV_DB_PATH", str(tmp_path / "conversations.db"))
    me = tmp_path / "workspace" / "me"
    me.mkdir(parents=True)
    (me / "interests_tech.md").write_text(_OLD_TECH, encoding="utf-8")
    (me / "interests_markets.md").write_text(_OLD_MARKETS, encoding="utf-8")


def _me(name: str) -> Path:
    return Path(os.environ["WORKSPACE_DIR"]) / "me" / name


def _write_report(category: str, stem: str, text: str) -> None:
    path = Path(os.environ["WORKSPACE_DIR"]) / "cycles" / category / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _weights(category: str, value: float = 0.5) -> dict:
    # Noise sits at its ceiling so these round-trip through sanitize_weights unchanged.
    return {n: value for n in triage.bucket_names(category)} | {"노이즈": 0.1}


def _reply(**overrides) -> str:
    data = {
        "interests_tech": _NEW_TECH,
        "interests_markets": _NEW_MARKETS,
        "triage_weights_tech": _weights("tech"),
        "triage_weights_markets": _weights("markets"),
        "changes": ["NEW-TECH ↑ (신규)", "기타경제 ↑"],
    }
    return json.dumps(data | overrides, ensure_ascii=False)


def _run(reply: str, date: str = "2026-05-08") -> list[str]:
    sent: list[str] = []
    with patch("newsparser.scripts.run_reflect.ask_claude", return_value=reply), \
         patch("newsparser.scripts.run_reflect.send_long_message",
               side_effect=lambda m: sent.append(m)):
        script.main(date)
    return sent


def test_reflect_schema_lists_every_bucket_plus_noise():
    schema = script.reflect_schema()
    s = schema["properties"]["triage_weights_tech"]
    assert set(s["required"]) == set(triage.bucket_names("tech")) and "노이즈" in s["required"]
    assert set(s["properties"]) == set(s["required"])
    assert s["additionalProperties"] is False
    assert all(p == {"type": "number"} for p in s["properties"].values())
    assert set(schema["required"]) == set(schema["properties"]) == {
        "interests_tech", "interests_markets",
        "triage_weights_tech", "triage_weights_markets", "changes",
    }
    assert schema["additionalProperties"] is False
    assert schema["properties"]["changes"] == {"type": "array", "items": {"type": "string"}}


def test_run_reflect_writes_profiles_weights_and_sends_changes():
    sent = _run(_reply())

    assert _me("interests_tech.md").read_text(encoding="utf-8") == _NEW_TECH
    assert _me("interests_markets.md").read_text(encoding="utf-8") == _NEW_MARKETS
    # Written weights must round-trip through the reader the cycles use.
    assert triage.load_weights("tech") == _weights("tech")
    assert triage.load_weights("markets") == _weights("markets")
    assert sent == ["[REFLECT] NEW-TECH ↑ (신규)\n기타경제 ↑"]


def test_run_reflect_enforces_weight_floors_and_ceiling():
    bad = _weights("markets", 0.0) | {"노이즈": 0.9}
    _run(_reply(triage_weights_markets=bad))

    out = triage.load_weights("markets")
    assert out["기타경제"] == 0.4 and out["노이즈"] == 0.1


def test_run_reflect_sends_no_change_notice_when_changes_empty():
    assert _run(_reply(changes=[])) == ["[REFLECT] 변경 없음"]


def test_run_reflect_keeps_profile_when_empty():
    _run(_reply(interests_tech=""))

    assert _me("interests_tech.md").read_text(encoding="utf-8") == _OLD_TECH
    assert _me("interests_markets.md").read_text(encoding="utf-8") == _NEW_MARKETS


def test_run_reflect_keeps_profile_without_table_rows():
    _run(_reply(interests_markets="표 없이 산문만 반환"))

    assert _me("interests_markets.md").read_text(encoding="utf-8") == _OLD_MARKETS
    assert _me("interests_tech.md").read_text(encoding="utf-8") == _NEW_TECH


def test_run_reflect_keeps_old_weights_when_a_bucket_is_missing():
    old = _weights("tech", 0.9)
    triage.weights_path("tech").write_text(json.dumps(old), encoding="utf-8")
    broken = _weights("tech")
    broken.pop("보안취약점")

    _run(_reply(triage_weights_tech=broken))

    assert triage.load_weights("tech") == old
    # The other category is independent.
    assert triage.load_weights("markets") == _weights("markets")


def test_run_reflect_prompt_keeps_triage_stats_line():
    graph = "\n## Graph updates\n### Entities\n- NEW | Company | GRAPH-ONLY | aliases: []\n"
    _write_report("tech", "2026-05-08-12", "TECH-BODY" + graph + "\n트리아지: 후보 9건\n")
    _write_report("markets", "2026-04-24-12", "OUT-OF-RANGE")
    _write_report("markets", "2026-04-25-12", "MARKETS-FIRST-DAY")

    calls: list[tuple[str, str, dict]] = []

    def fake_ask(prompt, system, **kw):
        calls.append((prompt, system, kw))
        return _reply()

    with patch("newsparser.scripts.run_reflect.ask_claude", side_effect=fake_ask), \
         patch("newsparser.scripts.run_reflect.send_long_message"):
        script.main("2026-05-08")

    assert len(calls) == 1
    prompt, system, kw = calls[0]
    assert "기준일 2026-05-08" in prompt
    assert "트리아지: 후보 9건" in prompt
    assert "## Graph updates" not in prompt and "GRAPH-ONLY" not in prompt
    assert '<report name="tech/2026-05-08-12">' in prompt
    # 14 days inclusive: 2026-04-25 .. 2026-05-08.
    assert "MARKETS-FIRST-DAY" in prompt and "OUT-OF-RANGE" not in prompt
    assert "<interests_tech>\n" + _OLD_TECH in prompt
    assert "<interests_markets>\n" + _OLD_MARKETS in prompt
    assert "<demand>" in prompt and "사용자 관심 수요 (최근 14일" in prompt
    assert "<bucket_axis>" in prompt and "노이즈" in prompt and "weights_files" not in prompt
    assert kw["timeout"] == 1500
    assert kw["output_schema"] == script.reflect_schema()
    assert "interest profiles" in system


@pytest.mark.parametrize("reply", [
    "not json",
    "[1, 2]",
    json.dumps({"interests_tech": _NEW_TECH}),
])
def test_run_reflect_invalid_output_raises_and_writes_nothing(reply):
    sent: list[str] = []

    with patch("newsparser.scripts.run_reflect.ask_claude", return_value=reply), \
         patch("newsparser.scripts.run_reflect.send_long_message",
               side_effect=lambda m: sent.append(m)), \
         pytest.raises(ClaudeError, match="reflect: invalid"):
        script.main("2026-05-08")

    assert _me("interests_tech.md").read_text(encoding="utf-8") == _OLD_TECH
    assert _me("interests_markets.md").read_text(encoding="utf-8") == _OLD_MARKETS
    assert not triage.weights_path("tech").exists()
    assert not triage.weights_path("markets").exists()
    assert sent == []
