"""Memory server 新 RPC 单测（P0）：Remember/Recall/ForgetUser/ExportUser。

走真实 proto 消息（gen/python 由根 conftest 注入 sys.path）+ MemoryServicer + 内存兜底，
校验 proto↔dict 映射与 server 接线。不连 PG / Redis。
"""
import asyncio
import importlib.util
import json
import os
import sys
import time
from types import SimpleNamespace

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
from memory.e2e_capability import sign_memory_capability  # noqa: E402
from scripts.e2e_identity import encode_secret  # noqa: E402


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
    assert p["user_id"] == "u1"
    assert p["occupant_id"] == "primary"


def test_memory_routine_dedup_is_scoped_per_user_and_occupant():
    svc = _servicer()
    published = []

    class _FakeNC:
        async def publish(self, subject, data):
            published.append(json.loads(data))

    svc._nc = _FakeNC()
    svc._nats_tried = True

    async def go():
        await svc._emit_proactive("买咖啡吗", "routine.buy_coffee", "u1", "primary")
        await svc._emit_proactive("买咖啡吗", "routine.buy_coffee", "u2", "passenger")

    asyncio.run(go())

    assert [item["user_id"] for item in published] == ["u1", "u2"]
    assert [item["occupant_id"] for item in published] == ["primary", "passenger"]
    assert published[0]["dedup_key"] != published[1]["dedup_key"]


def test_synthetic_sessions_skip_consolidation(monkeypatch):
    """合成会话（eval-/e2e-/replay- 等前缀）不触发 LLM 抽取巩固：不烧 token、
    不把测试对话沉淀进真实画像；客户端自报 memtest- 也不能绕过。"""
    svc = _servicer()
    calls = []

    async def fake_bg(*a, **k):
        calls.append(a)

    monkeypatch.setattr(svc, "_consolidate_bg", fake_bg)

    async def go():
        for prefix in ("eval-", "e2e-", "replay-", "nightly-", "memtest-"):
            for i in range(8):
                await svc.AppendTurn(memory_pb2.AppendTurnRequest(
                    session_id=f"{prefix}case", role="user", text=f"t{i}", user_id="u1"), None)
        assert calls == []  # 合成会话：多前缀 × 8 轮，零抽取

        for i in range(4):
            await svc.AppendTurn(memory_pb2.AppendTurnRequest(
                session_id="hmi-normal-1", role="user", text=f"t{i}", user_id="u1"), None)
        await asyncio.sleep(0)
        assert len(calls) == 1  # 真实会话照旧

    asyncio.run(go())


def test_only_runner_signed_synthetic_session_can_trigger_extraction(monkeypatch):
    secret = bytes(range(32))
    monkeypatch.setenv("E2E_CAPABILITY_ENABLED", "true")
    monkeypatch.setenv("E2E_CAPABILITY_SECRET", encode_secret(secret))
    svc = _servicer()
    calls = []

    async def fake_bg(*args):
        calls.append(args)

    monkeypatch.setattr(svc, "_consolidate_bg", fake_bg)
    run_id = "e2e-run-abc"
    user_id = f"{run_id}-e2e_memory"
    plain_session = f"{user_id}-session-1"
    signed_capability = sign_memory_capability(
        secret,
        run_id=run_id,
        user_id=user_id,
        session_id=plain_session,
        timeout_s=300,
        now=int(time.time()),
    )
    tampered_capability = (
        signed_capability[:-1]
        + ("A" if signed_capability[-1] != "A" else "B")
    )
    expired_capability = sign_memory_capability(
        secret,
        run_id=run_id,
        user_id=user_id,
        session_id=f"{user_id}-session-2",
        timeout_s=300,
        now=int(time.time()) - 1000,
    )
    wrong_secret_capability = sign_memory_capability(
        b"z" * 32,
        run_id=run_id,
        user_id=user_id,
        session_id=f"{user_id}-session-3",
        timeout_s=300,
        now=int(time.time()),
    )

    async def go():
        await svc.AppendTurn(memory_pb2.AppendTurnRequest(
            session_id="e2e-ordinary-session",
            role="user",
            text="记住普通 synthetic 仍然不抽取",
            user_id=user_id,
        ), None)
        assert calls == []

        for request_session, capability, request_user in (
            (plain_session, tampered_capability, user_id),
            (f"{user_id}-session-2", expired_capability, user_id),
            (f"{user_id}-session-3", wrong_secret_capability, user_id),
            (plain_session, signed_capability, f"{run_id}-another-case"),
        ):
            await svc.AppendTurn(memory_pb2.AppendTurnRequest(
                session_id=request_session,
                role="user",
                text="记住，这条无权触发抽取",
                user_id=request_user,
                e2e_memory_capability=capability,
            ), None)
        assert calls == []

        await svc.AppendTurn(memory_pb2.AppendTurnRequest(
            session_id=plain_session,
            role="user",
            text="记住，我喜欢26度",
            user_id=user_id,
            e2e_memory_capability=signed_capability,
        ), None)
        await asyncio.sleep(0)
        assert len(calls) == 1
        assert calls[0][0] == plain_session

    asyncio.run(go())


def test_memory_gate_checks_exact_run_derived_from_request_user(monkeypatch):
    secret = bytes(range(32))
    monkeypatch.setenv("E2E_CAPABILITY_ENABLED", "true")
    monkeypatch.setenv("E2E_CAPABILITY_SECRET", encode_secret(secret))
    svc = _servicer()
    request = memory_pb2.AppendTurnRequest(
        session_id="e2e-run-abc-e2e_memory-session-1",
        role="user",
        text="remember",
        user_id="e2e-run-abc-e2e_memory",
        e2e_memory_capability="e2emem.v1.opaque.signature",
    )

    monkeypatch.setattr(
        _mem_server,
        "verify_memory_capability",
        lambda *_args, **_kwargs: SimpleNamespace(
            run_id="e2e-run",
            user_id=request.user_id,
            session_id=request.session_id,
        ),
    )
    assert svc._allows_synthetic_extraction(request) is False

    monkeypatch.setattr(
        _mem_server,
        "verify_memory_capability",
        lambda *_args, **_kwargs: SimpleNamespace(
            run_id="e2e-run-abc",
            user_id=request.user_id,
            session_id=request.session_id,
        ),
    )
    assert svc._allows_synthetic_extraction(request) is True


def test_memory_gate_accepts_only_capability_bound_to_plain_request_session(
    monkeypatch,
):
    secret = bytes(range(32))
    monkeypatch.setenv("E2E_CAPABILITY_ENABLED", "true")
    monkeypatch.setenv("E2E_CAPABILITY_SECRET", encode_secret(secret))
    svc = _servicer()
    run_id = "e2e-run-abc"
    user_id = f"{run_id}-e2e_journeys"
    session_id = f"{user_id}-session-1"
    capability = sign_memory_capability(
        secret,
        run_id=run_id,
        user_id=user_id,
        session_id=session_id,
        timeout_s=300,
        now=int(time.time()),
    )

    valid = SimpleNamespace(
        session_id=session_id,
        user_id=user_id,
        e2e_memory_capability=capability,
    )
    assert svc._allows_synthetic_extraction(valid) is True

    wrong_session = SimpleNamespace(
        session_id=f"{user_id}-session-2",
        user_id=user_id,
        e2e_memory_capability=capability,
    )
    wrong_user = SimpleNamespace(
        session_id=session_id,
        user_id=f"{run_id}-other-case",
        e2e_memory_capability=capability,
    )
    assert svc._allows_synthetic_extraction(wrong_session) is False
    assert svc._allows_synthetic_extraction(wrong_user) is False


def test_memory_gate_rejects_forged_and_expired_dedicated_capabilities(
    monkeypatch,
):
    secret = bytes(range(32))
    monkeypatch.setenv("E2E_CAPABILITY_ENABLED", "true")
    monkeypatch.setenv("E2E_CAPABILITY_SECRET", encode_secret(secret))
    svc = _servicer()
    run_id = "e2e-run-abc"
    user_id = f"{run_id}-e2e_journeys"
    session_id = f"{user_id}-session-1"
    current = int(time.time())
    capability = sign_memory_capability(
        secret,
        run_id=run_id,
        user_id=user_id,
        session_id=session_id,
        timeout_s=300,
        now=current,
    )
    forged = capability[:-1] + ("A" if capability[-1] != "A" else "B")
    expired = sign_memory_capability(
        secret,
        run_id=run_id,
        user_id=user_id,
        session_id=session_id,
        timeout_s=300,
        now=current - 1000,
    )

    for token in (forged, expired):
        request = SimpleNamespace(
            session_id=session_id,
            user_id=user_id,
            e2e_memory_capability=token,
        )
        assert svc._allows_synthetic_extraction(request) is False


# ── EVA 二轮批 D（G7）：未来事件 → 询问式提醒建议 ─────────────────────────

def test_future_event_emits_ask_style_reminder_offer():
    """抽到带未来 event_time 的情景事件 → 发**询问式**提醒建议卡（零执行权：
    不自动建 reminder，「要的」按钮 send_text 回发正常语音链）；send_text 里的
    事件标题剥掉原时间词（防「8月X日15:00…周六下午三点…」双时间让解析咬错）；
    建议只在抽取当轮（new_ids 门控）发一次。"""
    import time as _time
    svc = _servicer()
    published = []

    class _FakeNC:
        async def publish(self, subject, data):
            published.append((subject, json.loads(data)))

    svc._nc = _FakeNC()
    svc._nats_tried = True
    future_ts = int(_time.time()) + 3 * 86400

    async def go():
        ids = await svc.store.remember([{
            "user_id": "u1", "kind": "episodic", "text": "女儿周六下午三点钢琴比赛",
            "scope": "episodic.general", "subject": "女儿",
            "value_json": json.dumps({"event_time": future_ts}, ensure_ascii=False)}])
        await svc._derive_and_emit("u1", "primary", new_ids=ids)
        await svc._derive_and_emit("u1", "primary", new_ids=[])   # 非当轮 → 不再建议

    asyncio.run(go())
    offers = [p for _s, p in published if p["type"] == "event_reminder_offer"]
    assert len(offers) == 1
    p = offers[0]
    assert "要到时候提前提醒你吗" in p["speech"]
    card = p["card"]
    assert card["type"] == "reminder_card" and card["context"] == "offer"
    yes = card["actions"][0]
    assert yes["label"] == "要的" and "提醒我" in yes["send_text"]
    assert "周六" not in yes["send_text"] and "三点" not in yes["send_text"]
    assert "钢琴比赛" in yes["send_text"]
    assert p["priority"] == "advisory"


def test_past_event_never_offered():
    """事件时刻已过 → 不发建议（future_events 确定性过滤）。"""
    import time as _time
    svc = _servicer()
    published = []

    class _FakeNC:
        async def publish(self, subject, data):
            published.append(json.loads(data))

    svc._nc = _FakeNC()
    svc._nats_tried = True

    async def go():
        ids = await svc.store.remember([{
            "user_id": "u1", "kind": "episodic", "text": "上周的钢琴比赛",
            "scope": "episodic.general",
            "value_json": json.dumps({"event_time": int(_time.time()) - 86400})}])
        await svc._derive_and_emit("u1", "primary", new_ids=ids)

    asyncio.run(go())
    assert not [p for p in published if p["type"] == "event_reminder_offer"]
