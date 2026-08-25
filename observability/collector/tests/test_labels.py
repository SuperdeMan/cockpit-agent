"""落域可观测 + 标注载体（数据飞轮 P0）：span→turns 合并、gold 标注、导出、保留豁免。"""
import time

from fastapi.testclient import TestClient

from observability.collector.db import ObsDB, _plan_summary_of
from observability.collector.server import create_app


# ── _plan_summary_of：attrs → (intents, plan_mode, edge_nlu, actionability) ──

def test_plan_summary_prefers_explicit_intents():
    intents, mode, _, _ = _plan_summary_of(
        {"intents": "nearby.search,info.weather", "plan_mode": "toolcall",
         "plan": '[{"intent": "junk.other"}]'})
    assert intents == "nearby.search,info.weather"
    assert mode == "toolcall"


def test_plan_summary_falls_back_to_plan_json():
    intents, mode, _, _ = _plan_summary_of(
        {"plan": '[{"intent": "hvac.set"}, {"intent": "media.play"}]',
         "plan_mode": "json"})
    assert intents == "hvac.set,media.play"
    assert mode == "json"


def test_plan_summary_truncated_or_gated_plan_gives_empty():
    # gate_content 截断的半截 JSON / off 档占位符 → 不产半截意图列表
    assert _plan_summary_of({"plan": '[{"intent": "a.b"}, {"inte'})[0] == ""
    assert _plan_summary_of({"plan": "<len=900 sha=abcd1234>"})[0] == ""
    assert _plan_summary_of("junk") == ("", "", "", "")


# ── M5 P2-D2 端云分歧：`!=` 后缀让「分歧」在扫描时可见（不必逐轮拉 span 详情）─────

def test_plan_summary_marks_edge_divergence():
    agree = {"intents": "hvac.set", "edge_nlu": "hvac.on|0.92", "edge_agree": "1"}
    diverge = {"intents": "chitchat.talk", "edge_nlu": "hvac.on|0.92", "edge_agree": "0"}
    assert _plan_summary_of(agree)[2] == "hvac.on|0.92"
    assert _plan_summary_of(diverge)[2] == "hvac.on|0.92!="     # 分歧才带后缀
    assert _plan_summary_of({"intents": "a.b"})[2] == ""        # 端侧没判 → 空，不占位


# ── B6 §2 可执行性 shadow：同款 `!=` 分歧后缀（分歧轮才是有信息量的样本）──────

def test_plan_summary_marks_actionability_divergence():
    agree = {"intents": "nearby.search", "actionability": "execute|0.90",
             "actionability_agree": "1"}
    diverge = {"intents": "navigation.navigate_to", "actionability": "clarify|0.85",
               "actionability_agree": "0"}
    assert _plan_summary_of(agree)[3] == "execute|0.90"
    assert _plan_summary_of(diverge)[3] == "clarify|0.85!="     # 分歧才带后缀
    assert _plan_summary_of({"intents": "a.b"})[3] == ""        # 没判 → 空，不占位


# ── span↔turn 合并（顺序无关） ───────────────────────────────────────────────

def _planning_span(trace_id: str, intents: str = "nearby.search",
                   mode: str = "toolcall") -> dict:
    return {"trace_id": trace_id, "span_id": "sp", "ts": 1001,
            "node": "cloud.planning",
            "attrs": {"intents": intents, "plan_mode": mode,
                      "actionability": "execute|0.90",
                      "actionability_agree": "1"}}


def test_merge_span_then_turn():
    db = ObsDB(":memory:")
    db.insert_span(_planning_span("tr-1"))
    db.insert_turn({"trace_id": "tr-1", "session_id": "s", "ts": 1000,
                    "user_text": "附近有什么咖啡店"})
    row = db.search_turns(q="咖啡")[0]
    assert row["intents"] == "nearby.search"
    assert row["plan_mode"] == "toolcall"
    assert row["actionability"] == "execute|0.90"      # B6 shadow 也随 span 合并进 turns
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


def test_edge_turn_intents_are_persisted_and_merge_with_cloud_planning():
    db = ObsDB(":memory:")
    db.insert_turn({
        "trace_id": "tr-edge", "session_id": "s", "ts": 1000,
        "user_text": "电量还有多少", "intents": "battery.query",
    })
    assert db.search_turns(q="电量")[0]["intents"] == "battery.query"

    db.insert_span(_planning_span("tr-edge", "info.weather", "toolcall"))
    assert db.search_turns(q="电量")[0]["intents"] == (
        "battery.query,info.weather")


def test_cloud_planning_intent_survives_later_edge_turn_upsert():
    db = ObsDB(":memory:")
    db.insert_span(_planning_span("tr-mixed", "reminder.create"))
    db.insert_turn({
        "trace_id": "tr-mixed", "session_id": "s", "ts": 1000,
        "intents": "hvac.off", "status": "ok",
    })

    assert db.search_turns()[0]["intents"] == "reminder.create,hvac.off"


def test_llm_request_pin_is_persisted_via_existing_span_contract():
    db = ObsDB(":memory:")
    db.insert_turn({"trace_id": "tr-pin", "session_id": "s", "ts": 1000})
    db.insert_llm({
        "trace_id": "tr-pin", "session_id": "s", "ts": 1001,
        "caller": "cloud-planner", "provider": "minimax",
        "model": "MiniMax-M3", "requested_tier": "MiniMax-M3",
        "pinned": True, "status": "ok",
    })

    detail = db.turn_detail("tr-pin")

    assert detail["llm_calls"][0]["pinned"] is True
    assert detail["llm_calls"][0]["requested_tier"] == "MiniMax-M3"
    assert any(span["node"] == "llm.call.meta" for span in detail["spans"])


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
