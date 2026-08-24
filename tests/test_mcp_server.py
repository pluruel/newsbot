import pytest
from freezegun import freeze_time
from pathlib import Path
from unittest.mock import patch

from newsparser.mcp_server import (
    graph_query, read_cycle_reports, read_conversation_history, read_interests,
    search_conversations, get_conversation_thread, clear_conversation_history,
)
from newsparser.store import conversations as conv


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("CONV_DB_PATH", str(tmp_path / "conversations.db"))
    conv.init_conv_db()
    (tmp_path / "workspace" / "me").mkdir(parents=True)
    (tmp_path / "workspace" / "cycles").mkdir(parents=True)


def test_graph_query_returns_formatted_context():
    with patch("newsparser.mcp_server.get_context", return_value=[
        {"name": "삼성전자", "label": "Company", "mentions": 5}
    ]), patch("newsparser.mcp_server.get_influence_chain", return_value=[]):
        result = graph_query("삼성전자")
    assert "삼성전자" in result


def test_graph_query_logs_interest_event():
    with patch("newsparser.mcp_server.get_context", return_value=[]), \
         patch("newsparser.mcp_server.get_influence_chain", return_value=[]):
        graph_query("TSMC")
    counts = dict(conv.interest_theme_counts())
    assert counts.get("TSMC") == 1


def test_read_cycle_reports_returns_n_most_recent(tmp_path):
    cycles = Path(tmp_path / "workspace" / "cycles" / "markets")
    cycles.mkdir(parents=True, exist_ok=True)
    (cycles / "2026-05-04-10.md").write_text("cycle A")
    (cycles / "2026-05-05-10.md").write_text("cycle B")
    (cycles / "2026-05-06-10.md").write_text("cycle C")

    result = read_cycle_reports(category="markets", n=2)
    assert "cycle B" in result
    assert "cycle C" in result
    assert "cycle A" not in result


def test_read_cycle_reports_empty_dir():
    result = read_cycle_reports(category="markets")
    assert "No cycle reports found" in result


def test_read_conversation_history_returns_formatted_turns(tmp_path):
    conv.add_message("chat99", "user", "안녕", ts="2026-05-05T00:00:00")
    conv.add_message("chat99", "assistant", "안녕하세요", ts="2026-05-05T00:00:01")
    result = read_conversation_history("chat99")
    assert "USER: 안녕" in result
    assert "ASSISTANT: 안녕하세요" in result


def test_read_conversation_history_empty():
    result = read_conversation_history("nonexistent_chat")
    assert "No conversation history" in result


def test_search_conversations_finds_turn():
    conv.add_message("chat99", "user", "엔비디아 실적 발표 언제야")
    conv.add_message("chat99", "assistant", "다음 주다")
    result = search_conversations("엔비디아")
    assert "엔비디아" in result
    result_none = search_conversations("존재하지않는키워드")
    assert "No conversation turns matching" in result_none


def test_get_conversation_thread_walks_reply_chain():
    uid = conv.add_message("chat99", "user", "질문")
    aid = conv.add_message("chat99", "assistant", "답변", reply_to_id=uid)
    result = get_conversation_thread(aid)
    assert "USER: 질문" in result
    assert "ASSISTANT: 답변" in result


def test_clear_conversation_history_scoped():
    conv.add_message("chatA", "user", "a")
    conv.add_message("chatB", "user", "b")
    result = clear_conversation_history("chatA")
    assert "1 turns" in result
    assert conv.get_recent_messages("chatA") == []
    assert len(conv.get_recent_messages("chatB")) == 1


def test_read_interests_returns_content(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)
    (me / "interests_tech.md").write_text("# Tech profile\n")
    result = read_interests(category="tech")
    assert "Tech profile" in result


def test_read_interests_missing_file():
    result = read_interests(category="tech")
    assert "(no interests file)" in result


from newsparser.mcp_server import (
    graph_query, read_cycle_reports, read_conversation_history, read_interests,
    write_interests, get_interest_weights, classify_query as mcp_classify_query,
)


def test_read_cycle_reports_reads_per_category_subfolder(tmp_path):
    base = Path(tmp_path / "workspace" / "cycles")
    (base / "tech").mkdir(parents=True, exist_ok=True)
    (base / "markets").mkdir(parents=True, exist_ok=True)
    (base / "tech" / "2026-05-07-12.md").write_text("tech cycle X")
    (base / "markets" / "2026-05-07-12.md").write_text("markets cycle Y")

    tech = read_cycle_reports(category="tech", n=2)
    assert "tech cycle X" in tech
    assert "markets cycle Y" not in tech


def test_graph_query_passes_category_filter():
    with patch("newsparser.mcp_server.get_context", return_value=[]) as mock_ctx, \
         patch("newsparser.mcp_server.get_influence_chain", return_value=[]):
        graph_query("OpenAI", category="tech")
    _, kwargs = mock_ctx.call_args
    args = mock_ctx.call_args[0]
    # accept either calling convention
    assert kwargs.get("category") == "tech" or "tech" in args


def test_read_interests_reads_per_category_file(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)
    (me / "interests_tech.md").write_text("# Tech profile\n")
    (me / "interests_markets.md").write_text("# Markets profile\n")

    assert "Tech profile" in read_interests(category="tech")
    assert "Markets profile" in read_interests(category="markets")


def test_write_interests_writes_to_per_category_file(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)

    write_interests(category="tech", content="# new tech profile\n")
    assert (me / "interests_tech.md").read_text() == "# new tech profile\n"


def test_get_interest_weights_uses_per_category_file(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)
    (me / "interests_tech.md").write_text(
        "| Theme | interest_weight | familiarity_weight | Notes |\n"
        "|---|---|---|---|\n"
        "| AI | 0.95 | 0.5 | |\n"
    )
    out = get_interest_weights(category="tech", days=14)
    assert "AI" in out
    assert "0.95" in out


def test_get_interest_weights_estimates_from_events():
    conv.log_interest_event("AI")
    conv.log_interest_event("AI")
    conv.log_interest_event("반도체")
    out = get_interest_weights(category="tech", days=14)
    assert "AI" in out and "반도체" in out


def test_clear_interest_events_tool():
    from newsparser.mcp_server import clear_interest_events
    conv.log_interest_event("AI")
    result = clear_interest_events()
    assert "cleared" in result
    assert conv.interest_theme_counts() == []


def test_classify_query_tool_returns_label():
    with patch("newsparser.mcp_server._classify_query_impl", return_value="tech"):
        result = mcp_classify_query("OpenAI 신모델 동향")
    assert result == "tech"


def test_read_cycle_reports_both_merges_categories(tmp_path):
    base = Path(tmp_path / "workspace" / "cycles")
    (base / "tech").mkdir(parents=True, exist_ok=True)
    (base / "markets").mkdir(parents=True, exist_ok=True)
    (base / "tech" / "2026-05-07-12.md").write_text("tech cycle X")
    (base / "markets" / "2026-05-07-13.md").write_text("markets cycle Y")

    out = read_cycle_reports(category="both", n=4)
    assert "tech cycle X" in out
    assert "markets cycle Y" in out


def test_read_cycle_reports_default_means_both(tmp_path):
    base = Path(tmp_path / "workspace" / "cycles")
    (base / "tech").mkdir(parents=True, exist_ok=True)
    (base / "tech" / "2026-05-07-12.md").write_text("tech cycle X")
    out = read_cycle_reports()
    assert "tech cycle X" in out


def test_graph_query_both_drops_category_filter():
    with patch("newsparser.mcp_server.get_context", return_value=[]) as mock_ctx, \
         patch("newsparser.mcp_server.get_influence_chain", return_value=[]):
        graph_query("OpenAI", category="both")
    kwargs = mock_ctx.call_args.kwargs
    assert kwargs.get("category") is None


def test_read_interests_both_returns_both(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)
    (me / "interests_tech.md").write_text("Tech profile")
    (me / "interests_markets.md").write_text("Markets profile")
    out = read_interests(category="both")
    assert "Tech profile" in out
    assert "Markets profile" in out


def test_mcp_server_entrypoint_uses_stdio():
    """mcp_server.__main__ block must call mcp.run(transport='stdio')."""
    import ast, inspect
    import newsparser.mcp_server as mod
    source = inspect.getsource(mod)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Call):
                    if any(
                        isinstance(kw.value, ast.Constant) and kw.value.value == "stdio"
                        for kw in stmt.keywords
                    ):
                        return
    pytest.fail("mcp.run(transport='stdio') not found in mcp_server.py")


def test_haiku_usage_reports_daily_rows_and_totals():
    from newsparser.mcp_server import haiku_usage
    from newsparser.store.sqlite import record_haiku_usage
    record_haiku_usage("triage", 700, 10)
    record_haiku_usage("triage", 300, 5)
    record_haiku_usage("classify_query", 40, 2)
    out = haiku_usage(days=1)
    assert "triage: 2 calls · in 1,000 tok · out 15 tok" in out
    assert "classify_query: 1 calls" in out
    assert "Totals by tag:" in out


def test_haiku_usage_empty():
    from newsparser.mcp_server import haiku_usage
    assert "No Haiku usage" in haiku_usage(days=1)


# --- project_conversation -----------------------------------------------------

def test_project_conversation_projects_the_last_n_turns():
    from newsparser.mcp_server import project_conversation

    conv.add_message("chat-yt", "user", "옛날 얘기")
    conv.add_message("chat-yt", "assistant", "옛날 답")
    conv.add_message("chat-yt", "user", "https://youtu.be/dQw4w9WgXcQ")
    conv.add_message("chat-yt", "assistant", "영상 요약")

    with patch("newsparser.graph.conversation_projector.project_message") as mock_project:
        result = project_conversation("chat-yt", n=2)

    projected = [call.args[0]["content"] for call in mock_project.call_args_list]
    assert projected == ["https://youtu.be/dQw4w9WgXcQ", "영상 요약"]
    assert "2 turn(s)" in result


def test_project_conversation_on_empty_chat_projects_nothing():
    from newsparser.mcp_server import project_conversation

    with patch("newsparser.graph.conversation_projector.project_message") as mock_project:
        result = project_conversation("no-such-chat")

    mock_project.assert_not_called()
    assert "No stored turns" in result


# --- ignore list tools -----------------------------------------------------
# The confirmation phrase these return is load-bearing: tracker._ADMIN_MARKERS
# matches on it to file the turn as `kind='admin'` and keep it out of the
# conversational history load_history returns. Failures must NOT carry it.

_IGNORE_MARKER = "ignore.md updated"


def test_add_ignore_writes_entry_and_returns_admin_marker():
    from newsparser.mcp_server import add_ignore, read_ignore
    result = add_ignore("entity", "TSMC", "반복 노이즈")
    assert _IGNORE_MARKER in result
    assert "TSMC" in read_ignore()


def test_add_ignore_rejects_bad_kind_without_admin_marker():
    """An unknown 종류 makes load_ignore skip the row silently — the tool must
    refuse, and must not look like a successful edit to _ADMIN_MARKERS."""
    from newsparser.mcp_server import add_ignore, read_ignore
    result = add_ignore("bogus", "TSMC")
    assert _IGNORE_MARKER not in result
    assert "TSMC" not in read_ignore()


def test_add_ignore_rejects_duplicate_without_admin_marker():
    from newsparser.mcp_server import add_ignore
    add_ignore("entity", "TSMC")
    result = add_ignore("entity", "tsmc")
    assert _IGNORE_MARKER not in result


def test_remove_ignore_drops_entry_and_returns_admin_marker():
    from newsparser.mcp_server import add_ignore, remove_ignore, read_ignore
    add_ignore("entity", "TSMC")
    result = remove_ignore("TSMC")
    assert _IGNORE_MARKER in result
    assert "TSMC" not in read_ignore()


def test_remove_ignore_no_match_reports_without_admin_marker():
    from newsparser.mcp_server import remove_ignore
    result = remove_ignore("없는것")
    assert _IGNORE_MARKER not in result


def test_read_ignore_never_carries_the_admin_marker():
    """Reading is not an edit — if it matched, every 차단 리스트 lookup would be
    dropped from conversation history."""
    from newsparser.mcp_server import add_ignore, read_ignore
    assert _IGNORE_MARKER not in read_ignore()          # empty list
    add_ignore("entity", "TSMC")
    assert _IGNORE_MARKER not in read_ignore()          # populated


def test_ignore_tool_markers_are_all_recognised_by_tracker():
    """Pin the two copies together: tracker filters on _ADMIN_MARKERS, the tools
    produce the phrase. Move one, move the other."""
    from newsparser.bot.tracker import _ADMIN_MARKERS
    from newsparser.mcp_server import add_ignore, remove_ignore
    added = add_ignore("entity", "TSMC")
    removed = remove_ignore("TSMC")
    for answer in (added, removed):
        assert any(m in answer for m in _ADMIN_MARKERS)


@freeze_time("2026-08-24 20:00:00")   # 2026-08-25 05:00 KST
def test_read_ignore_renders_age_in_kst_not_host_local():
    """add_ignore stamps KST; a host-local clock here would show "-1일 경과"
    for an entry added moments ago. The host this runs on is Etc/UTC."""
    from newsparser.mcp_server import add_ignore, read_ignore
    add_ignore("entity", "TSMC")
    assert "0일 경과" in read_ignore()
