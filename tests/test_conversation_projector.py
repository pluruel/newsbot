import os
import pytest

from newsparser.store import conversations as conv
from newsparser.graph import conversation_projector as proj


@pytest.fixture(autouse=True)
def tmp_conv_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CONV_DB_PATH", str(tmp_path / "conversations.db"))
    conv.init_conv_db()


def test_projection_is_best_effort_when_neo4j_absent(monkeypatch):
    # No NEO4J_PASSWORD → get_driver() raises; projection must swallow, not raise.
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    uid = conv.add_message("c1", "user", "질문")
    aid = conv.add_message("c1", "assistant", "답", reply_to_id=uid)
    proj.project_exchange("c1", uid, aid)      # must not raise
    proj.project_message(conv.get_message(uid))  # must not raise
    assert proj.messages_about_entity("Fed") == []  # swallows, returns []


# --- Integration (requires a disposable Neo4j) ------------------------------

_neo4j = pytest.mark.skipif(
    os.environ.get("NEWSPARSER_TEST_NEO4J") != "1",
    reason="Set NEWSPARSER_TEST_NEO4J=1 with NEO4J_URI at a disposable instance.",
)


@_neo4j
def test_reproject_builds_reply_and_mention_edges():
    from newsparser.graph.neo4j_client import get_driver, close_driver
    os.environ.setdefault("NEO4J_PASSWORD", "testpass")
    with get_driver().session() as s:
        s.run("MATCH (n) DETACH DELETE n")
        s.run("MERGE (:Institution {canonical_name: 'Fed'})")

    uid = conv.add_message("c1", "user", "Fed 발표 어땠어")
    conv.add_message("c1", "assistant", "금리 동결", reply_to_id=uid)
    n = proj.reproject_all()
    assert n == 2

    with get_driver().session() as s:
        replies = s.run(
            "MATCH (:Message)-[:REPLIES_TO]->(:Message) RETURN count(*) AS c"
        ).single()["c"]
        mentions = s.run(
            "MATCH (:Message)-[:MENTIONS]->(:Institution {canonical_name:'Fed'}) "
            "RETURN count(*) AS c"
        ).single()["c"]
    assert replies == 1
    assert mentions == 1
    assert any(m["content"] == "Fed 발표 어땠어"
               for m in proj.messages_about_entity("Fed"))
    close_driver()
