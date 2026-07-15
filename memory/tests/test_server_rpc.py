"""Memory server 新 RPC 单测（P0）：Remember/Recall/ForgetUser/ExportUser。

走真实 proto 消息（gen/python 由根 conftest 注入 sys.path）+ MemoryServicer + 内存兜底，
校验 proto↔dict 映射与 server 接线。不连 PG / Redis。
"""
import asyncio
import importlib.util
import json
import os
import sys

_MEM_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _MEM_DIR)
from cockpit.memory.v1 import memory_pb2  # noqa: E402

# memory/server.py 的裸模块名 'server' 与 orchestrator/edge/server.py 冲突。
# 用唯一名加载，避免污染 sys.modules['server'] 破坏 edge 测试收集。
_spec = importlib.util.spec_from_file_location(
    "memory_server_under_test", os.path.join(_MEM_DIR, "server.py"))
_mem_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mem_server)
MemoryServicer = _mem_server.MemoryServicer


def _servicer() -> MemoryServicer:
    svc = MemoryServicer()
    svc.store.url = ""            # Redis 内存兜底
    svc.store._vstore._dsn = ""   # 强制向量存储内存兜底（不连 PG）
    return svc


def _item(**kw):
    return memory_pb2.MemoryItem(**kw)


def test_remember_then_recall_rpc():
    svc = _servicer()

    async def go():
        req = memory_pb2.RememberRequest(items=[
            _item(user_id="u1", kind="semantic", text="用户不吃辣",
                  predicate="taste.spicy", scope="profile.taste", confidence=1.0),
        ])
        r = await svc.Remember(req, None)
        assert r.ok and len(r.ids) == 1
        return await svc.Recall(
            memory_pb2.RecallRequest(user_id="u1", query="辣"), None)

    rec = asyncio.run(go())
    assert len(rec.items) == 1
    assert rec.items[0].predicate == "taste.spicy"
    assert rec.items[0].text == "用户不吃辣"
    assert rec.scores[0] > 0


def test_remember_skips_items_without_user():
    svc = _servicer()
    r = asyncio.run(svc.Remember(
        memory_pb2.RememberRequest(items=[_item(text="无主语")]), None))
    assert r.ok is False and len(r.ids) == 0


def test_recall_requires_user_id():
    svc = _servicer()
    rec = asyncio.run(svc.Recall(memory_pb2.RecallRequest(query="辣"), None))
    assert len(rec.items) == 0


def test_export_then_forget_rpc():
    svc = _servicer()

    async def go():
        await svc.Remember(memory_pb2.RememberRequest(items=[
            _item(user_id="u1", kind="semantic", text="用户不吃辣",
                  predicate="taste.spicy", scope="profile.taste", confidence=1.0)]), None)
        exported = await svc.ExportUser(memory_pb2.ExportUserRequest(user_id="u1"), None)
        forgot = await svc.ForgetUser(memory_pb2.ForgetUserRequest(user_id="u1"), None)
        after = await svc.Recall(memory_pb2.RecallRequest(user_id="u1", query="辣"), None)
        return exported, forgot, after

    exported, forgot, after = asyncio.run(go())
    data = json.loads(exported.json)
    assert data["memories"] and data["memories"][0]["predicate"] == "taste.spicy"
    assert forgot.ok and forgot.deleted == 1
    assert len(after.items) == 0


def test_appendturn_triggers_consolidate_every_n():
    svc = _servicer()
    calls = []

    async def fake_consolidate(session_id, user_id, occupant_id="primary", vehicle_id=""):
        calls.append((session_id, user_id))
        return []

    svc.store.consolidate = fake_consolidate

    async def go():
        for i in range(4):  # 第 4 轮触发一次
            await svc.AppendTurn(memory_pb2.AppendTurnRequest(
                session_id="s1", role="user", text=f"t{i}", user_id="u1"), None)
        await asyncio.gather(*list(svc._bg))

    asyncio.run(go())
    assert calls == [("s1", "u1")]


def test_appendturn_explicit_remember_triggers_immediately():
    """B3-3 M2：「记住，我最喜欢…」这类会话往往 2 轮就结束，凑不满 4 轮节流窗——
    显式记忆陈述（用户轮）须立即触发抽取，且触发后重新计数不双跑。"""
    svc = _servicer()
    calls = []

    async def fake_consolidate(session_id, user_id, occupant_id="primary", vehicle_id=""):
        calls.append(session_id)
        return []

    svc.store.consolidate = fake_consolidate

    async def go():
        await svc.AppendTurn(memory_pb2.AppendTurnRequest(
            session_id="s1", role="user",
            text="记住，我最喜欢的空调温度是26度", user_id="u1"), None)
        await svc.AppendTurn(memory_pb2.AppendTurnRequest(
            session_id="s1", role="assistant", text="好嘞，已经记住啦", user_id="u1"), None)
        await asyncio.gather(*list(svc._bg))
        assert calls == ["s1"]              # 第 1 轮即触发；助手轮不重复触发

        # 助手复述「记住」不触发（role 门控）；普通轮回归 4 轮节流
        await svc.AppendTurn(memory_pb2.AppendTurnRequest(
            session_id="s2", role="assistant", text="记住了哦", user_id="u1"), None)
        if svc._bg:
            await asyncio.gather(*list(svc._bg))
        assert calls == ["s1"]

    asyncio.run(go())


def test_appendturn_without_userid_never_triggers():
    svc = _servicer()
    calls = []

    async def fake_consolidate(*a, **k):
        calls.append(1)
        return []

    svc.store.consolidate = fake_consolidate

    async def go():
        for i in range(8):  # 端侧本地轮无 user_id → 不触发抽取
            await svc.AppendTurn(memory_pb2.AppendTurnRequest(
                session_id="s2", role="user", text="x"), None)
        if svc._bg:
            await asyncio.gather(*list(svc._bg))

    asyncio.run(go())
    assert calls == []


def test_derive_and_emit_publishes_proactive():
    """#3：情景事件成 routine → derive → 发 agent.proactive 主动建议。"""
    svc = _servicer()
    published = []

    class _FakeNC:
        async def publish(self, subject, data):
            published.append((subject, data))

    svc._nc = _FakeNC()
    svc._nats_tried = True

    async def go():
        for _ in range(3):
            await svc.store.remember([{
                "user_id": "u1", "kind": "episodic", "text": "在公司附近星巴克买咖啡",
                "scope": "episodic.general",
                "value_json": json.dumps({"action": "买咖啡", "place": "公司附近星巴克",
                                          "hour": 8}, ensure_ascii=False)}])
        await svc._derive_and_emit("u1", "primary")

    asyncio.run(go())
    assert published and published[0][0] == "agent.proactive"
    p = json.loads(published[0][1])
    assert p["type"] == "routine_suggestion" and p["speech"] and p["agent_id"] == "memory"


def test_synthetic_sessions_skip_consolidation(monkeypatch):
    """合成会话（eval-/e2e-/replay- 等前缀）不触发 LLM 抽取巩固：不烧 token、
    不把测试对话沉淀进真实画像（conventions §9.2）；正常/memtest- 会话照旧 4 轮一触发。"""
    svc = _servicer()
    calls = []

    async def fake_bg(*a, **k):
        calls.append(a)

    monkeypatch.setattr(svc, "_consolidate_bg", fake_bg)

    async def go():
        for prefix in ("eval-", "e2e-", "replay-", "nightly-"):
            for i in range(8):
                await svc.AppendTurn(memory_pb2.AppendTurnRequest(
                    session_id=f"{prefix}case", role="user", text=f"t{i}", user_id="u1"), None)
        assert calls == []  # 合成会话：多前缀 × 8 轮，零抽取

        for i in range(4):
            await svc.AppendTurn(memory_pb2.AppendTurnRequest(
                session_id="memtest-routine-x", role="user", text=f"t{i}", user_id="u1"), None)
        await asyncio.sleep(0)  # 让后台 task 执行
        assert len(calls) == 1  # 抽取自测前缀不受影响

        for i in range(4):
            await svc.AppendTurn(memory_pb2.AppendTurnRequest(
                session_id="hmi-normal-1", role="user", text=f"t{i}", user_id="u1"), None)
        await asyncio.sleep(0)
        assert len(calls) == 2  # 真实会话照旧

    asyncio.run(go())
