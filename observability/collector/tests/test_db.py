"""ObsDB：SQLite 持久层（turns/spans/llm/logs 写入、查询、badcase、保留期清理）。"""
import time

from observability.collector.db import ObsDB


def _db():
    return ObsDB(path=":memory:")


def _turn(trace_id="t1", session_id="s1", ts=1000, **kw):
    return {"trace_id": trace_id, "session_id": session_id, "ts": ts,
            "user_text": kw.get("user_text", "打开空调"),
            "speech": kw.get("speech", "好的"),
            "status": kw.get("status", "ok"), "path": kw.get("path", "local"),
            "duration_ms": kw.get("duration_ms", 12.5),
            "actions": kw.get("actions", 1),
            "is_confirmation": kw.get("is_confirmation", False)}


def test_turn_roundtrip_and_session_aggregation():
    db = _db()
    db.insert_turn(_turn("t1", "s1", 1000))
    db.insert_turn(_turn("t2", "s1", 2000, status="rejected"))
    db.insert_turn(_turn("t3", "s2", 3000, status="err"))

    sessions = db.sessions()
    assert [s["session_id"] for s in sessions] == ["s2", "s1"]  # 最近活跃倒序
    s1 = next(s for s in sessions if s["session_id"] == "s1")
    assert s1["turns"] == 2 and s1["rejected"] == 1 and s1["errors"] == 0

    turns = db.session_turns("s1")
    assert [t["trace_id"] for t in turns] == ["t1", "t2"]  # 会话内时间正序


def test_turn_upsert_preserves_badcase_mark():
    db = _db()
    db.insert_turn(_turn("t1"))
    assert db.set_badcase("t1", True, "答非所问") is True
    # 同 trace 事件重复到达（重试/重播）——运行字段覆盖，人工标记保留
    db.insert_turn(_turn("t1", speech="新话术"))
    detail = db.turn_detail("t1")
    assert detail["turn"]["badcase"] == 1
    assert detail["turn"]["note"] == "答非所问"
    assert detail["turn"]["speech"] == "新话术"


def test_turn_detail_joins_spans_llm_logs():
    db = _db()
    db.insert_turn(_turn("t1"))
    db.insert_span({"trace_id": "t1", "span_id": "a", "ts": 1001,
                    "node": "route.cloud", "status": "ok",
                    "attrs": {"intent": "weather.query"}})
    db.insert_llm({"trace_id": "t1", "ts": 1002, "caller": "cloud-planner",
                   "model": "mimo", "prompt_tokens": 10, "completion_tokens": 5,
                   "latency_ms": 300})
    db.insert_log({"trace_id": "t1", "ts": 1003, "service": "cloud",
                   "level": "WARNING", "logger": "x", "msg": "boom"})

    detail = db.turn_detail("t1")
    assert detail["turn"]["user_text"] == "打开空调"
    assert detail["spans"][0]["attrs"]["intent"] == "weather.query"
    assert detail["llm_calls"][0]["model"] == "mimo"
    assert detail["logs"][0]["msg"] == "boom"
    assert db.turn_detail("nope") is None


def test_search_by_text_status_badcase():
    db = _db()
    db.insert_turn(_turn("t1", user_text="导航去机场", status="ok"))
    db.insert_turn(_turn("t2", user_text="打开天窗", status="err", ts=2000))
    db.set_badcase("t2", True)

    assert [t["trace_id"] for t in db.search_turns(q="机场")] == ["t1"]
    assert [t["trace_id"] for t in db.search_turns(status="err")] == ["t2"]
    assert [t["trace_id"] for t in db.search_turns(badcase=True)] == ["t2"]
    # trace_id 前缀直达（HMI 复制的短 id）
    assert [t["trace_id"] for t in db.search_turns(q="t1")] == ["t1"]


def test_sessions_text_filter():
    db = _db()
    db.insert_turn(_turn("t1", "s1", user_text="讲个笑话"))
    db.insert_turn(_turn("t2", "s2", user_text="导航去机场"))
    assert [s["session_id"] for s in db.sessions(q="机场")] == ["s2"]


def test_logs_query_filters():
    db = _db()
    db.insert_log({"ts": 1, "service": "edge", "level": "INFO", "msg": "a"})
    db.insert_log({"ts": 2, "service": "cloud", "level": "WARNING", "msg": "bad thing",
                   "trace_id": "t9"})
    assert len(db.query_logs(service="cloud")) == 1
    assert len(db.query_logs(level="warning")) == 1
    assert len(db.query_logs(trace_id="t9")) == 1
    assert db.query_logs(q="bad")[0]["msg"] == "bad thing"


def test_cleanup_exempts_badcase():
    db = _db()
    now_ms = int(time.time() * 1000)
    old = now_ms - 30 * 86400 * 1000
    db.insert_turn(_turn("old-plain", "s1", old))
    db.insert_turn(_turn("old-bad", "s1", old + 1))
    db.insert_turn(_turn("fresh", "s1", now_ms))
    db.insert_span({"trace_id": "old-bad", "ts": old, "node": "x"})
    db.insert_span({"trace_id": "old-plain", "ts": old, "node": "x"})
    db.set_badcase("old-bad", True)

    deleted = db.cleanup(retention_days=7)

    assert deleted == 1
    assert db.turn_detail("old-plain") is None
    assert db.turn_detail("old-bad")["turn"]["badcase"] == 1
    assert db.turn_detail("old-bad")["spans"]  # badcase 的链路数据同样豁免
    assert db.turn_detail("fresh") is not None


def test_llm_summary_groups_and_blindspot_label():
    """LLM 消耗归属汇总（dashboard「LLM」视图）：caller×model 分组、token 降序、
    空 caller 显示「(未归属)」（盲区盯防）、窗口过滤生效。"""
    db = _db()
    now_ms = int(time.time() * 1000)
    mk = lambda **kw: {"trace_id": "t", "ts": now_ms, "model": "m1",
                       "prompt_tokens": 100, "completion_tokens": 10,
                       "latency_ms": 50, "status": "ok", **kw}
    db.insert_llm(mk(caller="cloud-planner"))
    db.insert_llm(mk(caller="cloud-planner", prompt_tokens=300, status="err"))
    db.insert_llm(mk(caller="memory-extract", model="m2", prompt_tokens=50))
    db.insert_llm(mk(caller=""))                                  # 归属盲区
    db.insert_llm(mk(caller="old", ts=now_ms - 48 * 3600 * 1000))  # 窗口外

    # 多笔小额之和 > 单笔大额：暴露「ORDER BY 裸列名取组内任意行而非 SUM」的坑
    db.insert_llm(mk(caller="many-small", prompt_tokens=250))
    db.insert_llm(mk(caller="many-small", prompt_tokens=250))

    out = db.llm_summary(hours=24)
    groups = {(g["caller"], g["model"]): g for g in out["groups"]}
    assert ("old", "m1") not in groups                     # 48h 前不进 24h 窗
    planner = groups[("cloud-planner", "m1")]
    assert planner["calls"] == 2 and planner["prompt_tokens"] == 400
    assert planner["errors"] == 1 and planner["completion_tokens"] == 20
    assert ("(未归属)", "m1") in groups                     # 空 caller 标注盲区
    order = [g["caller"] for g in out["groups"]]
    assert order[0] == "many-small" and order[1] == "cloud-planner"  # 按 SUM 降序
