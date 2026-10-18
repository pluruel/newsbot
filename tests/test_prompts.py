from newsparser import prompts


def test_style_rules_reads_claude_md_style_section():
    s = prompts.style_rules()
    assert "Korean by default" in s and "No filler phrases" in s
    assert "MCP tools" not in s and "## Style" not in s


def test_system_prompt_has_no_tool_instructions():
    for name in ("cycle", "weekly", "reflect"):
        p = prompts.system_prompt(name)
        for banned in ("$ARGUMENTS", "Glob", "Read `", ".venv/bin/python", "stdout", "start_job"):
            assert banned not in p, (name, banned)


def test_cycle_prompt_keeps_report_format_verbatim():
    p = prompts.system_prompt("cycle")
    assert "• (중요도 0.NN) 한 줄 헤드라인. 본문 2–4문장." in p
    assert "## Graph updates" in p and "유효 Predicate: INFLUENCES" in p
