"""落域可观测 + 标注载体（数据飞轮 P0）：span→turns 合并、gold 标注、导出、保留豁免。"""
import time

from fastapi.testclient import TestClient

from observability.collector.db import ObsDB, _plan_summary_of
from observability.collector.server import create_app


# ── _plan_summary_of：attrs → (intents, plan_mode) ──────────────────────────

def test_plan_summary_prefers_explicit_intents():
    intents, mode = _plan_summary_of(
        {"intents": "nearby.search,info.weather", "plan_mode": "toolcall",
         "plan": '[{"intent": "junk.other"}]'})
    assert intents == "nearby.search,info.weather"
    assert mode == "toolcall"


def test_plan_summary_falls_back_to_plan_json():
    intents, mode = _plan_summary_of(
        {"plan": '[{"intent": "hvac.set"}, {"intent": "media.play"}]',
         "plan_mode": "json"})
    assert intents == "hvac.set,media.play"
    assert mode == "json"


def test_plan_summary_truncated_or_gated_plan_gives_empty():
    # gate_content 截断的半截 JSON / off 档占位符 → 不产半截意图列表
    assert _plan_summary_of({"plan": '[{"intent": "a.b"}, {"inte'})[0] == ""
    assert _plan_summary_of({"plan": "<len=900 sha=abcd1234>"})[0] == ""
    assert _plan_summary_of("junk") == ("", "")


# ── span↔turn 合并（顺序无关） ───────────────────────────────────────────────

def _planning_span(trace_id: str, intents: str = "nearby.search",
                   mode: str = "toolcall") -> dict:
    return {"trace_id": trace_id, "span_id": "sp", "ts": 1001,
            "node": "cloud.planning",
            "attrs": {"intents": intents, "plan_mode": mode}}


def test_merge_span_then_turn():
    db = ObsDB(":memory:")
    db.insert_span(_planning_span("tr-1"))
    db.insert_turn({"trace_id": "tr-1", "session_id": "s", "ts": 1000,
                    "user_text": "附近有什么咖啡店"})
    row = db.search_turns(q="咖啡")[0]
    assert row["intents"] == "nearby.search"
    assert row["plan_mode"] == "toolcall"
    assert row["user_text"] == "附近有什么咖啡店"


def test_merge_turn_then_span_and_reupsert_keeps_merge():
    db = ObsDB(":memory:")
    db.insert_turn({"trace_id": "tr-2", "session_id": "s", "ts": 1000,
                    "user_text": "导航去公司"})
    db.insert_span(_planning_span("tr-2", "navigation.navigate_to", "json"))
    # turn 事件重复到达（UPSERT 覆盖运行字段）不得抹掉合并列
    db.insert_turn({"trace_id": "tr-2", "session_id": "s", "ts": 1000,
                    "user_text": "导航去公司", "status": "ok"})
    row = db.search_turns(q="公司")[0]
    assert row["intents"] == "navigation.navigate_to"
    assert row["plan_mode"] == "json"


def test_non_planning_span_does_not_touch_turns():
    db = ObsDB(":memory:")
    db.insert_span({"trace_id": "tr-3", "span_id": "x", "ts": 1,
                    "node": "step.execute", "attrs": {"intents": "junk"}})
    assert db.search_turns() == []


# ── gold 标注 + 导出 + 清单（REST 全链） ─────────────────────────────────────

def _client() -> TestClient:
    client = TestClient(create_app())
    db = client.app.state.db
    db.insert_turn({"trace_id": "tr-1", "session_id": "s", "ts": 1000,
                    "user_text": "帮我看看附近有什么咖啡店"})
    db.insert_span(_planning_span("tr-1", "vision.describe", "toolcall"))
    return client


def test_label_flow_and_export():
    client = _client()
    ok = client.post("/api/turns/tr-1/label",
                     json={"gold_intents": ["nearby.search"]}).json()
    assert ok["ok"] is True and ok["gold_intents"] == "nearby.search"

    exported = client.get("/api/export/labels").json()
    assert exported["count"] == 1
    row = exported["labels"][0]
    assert row["user_text"] == "帮我看看附近有什么咖啡店"
    assert row["gold_intents"] == "nearby.search"
    assert row["intents"] == "vision.describe"       # 实际落域一并导出（错误对照）

    # 逗号串形态（服务端规范化）+ 清除
    client.post("/api/turns/tr-1/label", json={"gold_intents": "a.b, c.d"})
    assert client.get("/api/export/labels").json()["labels"][0]["gold_intents"] == "a.b,c.d"
    client.post("/api/turns/tr-1/label", json={"gold_intents": ""})
    assert client.get("/api/export/labels").json()["count"] == 0


def test_export_labels_not_shadowed_by_trace_export():
    """路由顺序：/api/export/labels 必须先于 /api/export/{trace_id} 注册。"""
    body = _client().get("/api/export/labels").json()
    assert "labels" in body and "error" not in body


def test_observed_intents_union():
    client = _client()
    client.post("/api/turns/tr-1/label", json={"gold_intents": "nearby.search"})
    options = client.get("/api/intents/observed").json()
    assert "vision.describe" in options and "nearby.search" in options


def test_cleanup_exempts_gold_labeled():
    db = ObsDB(":memory:")
    old_ts = int((time.time() - 30 * 86400) * 1000)
    db.insert_turn({"trace_id": "old-plain", "session_id": "s", "ts": old_ts,
                    "user_text": "过期普通轮"})
    db.insert_turn({"trace_id": "old-gold", "session_id": "s", "ts": old_ts,
                    "user_text": "过期已标注轮"})
    db.set_gold("old-gold", "nearby.search")
    deleted = db.cleanup(retention_days=7)
    assert deleted == 1
    remaining = [t["trace_id"] for t in db.search_turns(limit=10)]
    assert remaining == ["old-gold"]
