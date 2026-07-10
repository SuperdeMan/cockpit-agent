"""collector 会话/轮次/日志/导出 REST API（P0 新增，SQLite 数据源）。"""
from fastapi.testclient import TestClient

from observability.collector.server import create_app


def _client():
    client = TestClient(create_app())
    db = client.app.state.db
    db.insert_turn({"trace_id": "tr-1", "session_id": "sess-a", "ts": 1000,
                    "user_text": "导航去机场", "speech": "已开始导航",
                    "status": "ok", "path": "cloud", "duration_ms": 1500})
    db.insert_turn({"trace_id": "tr-2", "session_id": "sess-a", "ts": 2000,
                    "user_text": "你说呢", "speech": "", "status": "rejected"})
    db.insert_span({"trace_id": "tr-1", "span_id": "sp1", "ts": 1001,
                    "node": "cloud.planning", "status": "ok",
                    "attrs": {"steps": 1}})
    db.insert_llm({"trace_id": "tr-1", "ts": 1002, "caller": "cloud-planner",
                   "model": "mimo", "latency_ms": 400})
    db.insert_log({"trace_id": "tr-1", "ts": 1003, "service": "cloud-planner",
                   "level": "INFO", "msg": "Plan ready"})
    return client


def test_sessions_list():
    response = _client().get("/api/sessions")
    assert response.status_code == 200
    sessions = response.json()
    assert sessions[0]["session_id"] == "sess-a"
    assert sessions[0]["turns"] == 2
    assert sessions[0]["rejected"] == 1


def test_session_turns_ordered():
    turns = _client().get("/api/sessions/sess-a/turns").json()
    assert [t["trace_id"] for t in turns] == ["tr-1", "tr-2"]


def test_turn_detail_and_export():
    client = _client()
    detail = client.get("/api/turns/tr-1").json()
    assert detail["turn"]["user_text"] == "导航去机场"
    assert detail["spans"][0]["node"] == "cloud.planning"
    assert detail["llm_calls"][0]["model"] == "mimo"
    assert detail["logs"][0]["msg"] == "Plan ready"

    exported = client.get("/api/export/tr-1").json()
    assert exported["turn"]["trace_id"] == "tr-1"
    assert "exported_at" in exported

    assert client.get("/api/turns/none").json() == {"error": "not found"}


def test_search_and_badcase_flow():
    client = _client()
    hits = client.get("/api/search", params={"q": "机场"}).json()
    assert [t["trace_id"] for t in hits] == ["tr-1"]

    ok = client.post("/api/turns/tr-1/badcase",
                     json={"badcase": True, "note": "路线不对"}).json()
    assert ok["ok"] is True
    flagged = client.get("/api/search", params={"badcase": 1}).json()
    assert flagged[0]["trace_id"] == "tr-1"
    assert flagged[0]["note"] == "路线不对"


def test_logs_endpoint():
    logs = _client().get("/api/logs", params={"trace_id": "tr-1"}).json()
    assert len(logs) == 1 and logs[0]["service"] == "cloud-planner"
