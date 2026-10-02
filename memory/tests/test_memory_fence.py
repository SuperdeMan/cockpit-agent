"""CA2-15 S1b：删除与在途写入同代际失效（Memory 侧栅栏）。

走真实 proto 消息 + MemoryServicer + 内存兜底；竞态用例用事件把窗口真正撑开，而不是靠调度碰运气
（内存存储没有真实 await，不撑开窗口的并发用例会假绿）。
"""
import asyncio
import importlib.util
import json
import os
import sys

_MEM_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _MEM_DIR)
from cockpit.memory.v1 import memory_pb2  # noqa: E402
from fence import STALE, OwnerFence, StaleEpoch, eligible, generation  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "memory_server_fence_under_test", os.path.join(_MEM_DIR, "server.py"))
_mem_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mem_server)

UID = "u-fence"
PREF = json.dumps([{"category": "explicit_preference", "kind": "semantic",
                    "predicate": "climate.temperature", "text": "用户喜欢空调26度",
                    "scope": "profile.comfort", "confidence": 0.9}])


def _servicer():
    svc = _mem_server.MemoryServicer()
    svc.store.url = ""            # Redis 内存兜底
    svc.store._vstore._dsn = ""   # 向量存储内存兜底
    return svc


async def _epoch(svc, uid=UID):
    return (await svc.GetMemoryEpoch(memory_pb2.GetMemoryEpochRequest(user_id=uid), None)).memory_epoch


async def _turn(svc, text, epoch="", sid="s1", role="user", turn_id=""):
    return await svc.AppendTurn(memory_pb2.AppendTurnRequest(
        session_id=sid, role=role, text=text, user_id=UID, occupant_id="primary",
        turn_id=turn_id, memory_epoch=epoch), None)


async def _turns(svc, sid="s1"):
    resp = await svc.GetSession(memory_pb2.GetSessionRequest(
        session_id=sid, last_n=20, user_id=UID, occupant_id="primary"), None)
    return [t.text for t in resp.turns]


async def _memories(svc):
    return [d["text"] for d, _ in await svc.store.recall(UID, occupant_id="primary", query="")]


def test_epoch_is_lazy_and_every_kind_of_delete_advances_it():
    svc = _servicer()

    async def go():
        seen = [await _epoch(svc)]
        assert seen[0] == await _epoch(svc)                  # 读不推进
        deletes = [
            svc.ForgetUser(memory_pb2.ForgetUserRequest(user_id=UID, scopes=["profile.comfort"]), None),
            svc.ForgetUser(memory_pb2.ForgetUserRequest(user_id=UID, occupant_id="occ-2"), None),
            svc.ForgetUser(memory_pb2.ForgetUserRequest(user_id=UID), None),
            svc.DeleteVoiceprint(memory_pb2.DeleteVoiceprintRequest(
                user_id=UID, occupant_id="occ-2", purge_memory=True), None),
            svc.DeleteMemoryItem(memory_pb2.DeleteMemoryItemRequest(
                user_id=UID, occupant_id="primary", item_id="missing"), None),
        ]
        for call in deletes:
            await call
            seen.append(await _epoch(svc))
        return seen

    seen = asyncio.run(go())
    assert [generation(e) for e in seen] == [0, 1, 2, 3, 4, 5]
    assert len(set(seen)) == len(seen)


def test_a_write_observed_before_a_delete_is_refused_and_leaves_nothing():
    svc = _servicer()

    async def go():
        before = await _epoch(svc)
        await svc.ForgetUser(memory_pb2.ForgetUserRequest(user_id=UID), None)
        turn = await _turn(svc, "我家在云岚小区", epoch=before)
        remembered = await svc.Remember(memory_pb2.RememberRequest(items=[memory_pb2.MemoryItem(
            user_id=UID, occupant_id="primary", kind="semantic", text="家在云岚小区",
            predicate="place.home")], memory_epoch=before), None)
        profile = await svc.UpsertProfile(memory_pb2.UpsertProfileRequest(
            user_id=UID, key="news_active", value_json="{}", memory_epoch=before), None)
        return turn, remembered, profile, await _turns(svc), await _memories(svc)

    turn, remembered, profile, turns, memories = asyncio.run(go())
    assert (turn.ok, turn.error) == (False, STALE)
    assert (remembered.ok, remembered.error, list(remembered.ids)) == (False, STALE, [])
    assert (profile.ok, profile.error) == (False, STALE)
    assert turns == [] and memories == []


def test_current_and_legacy_writes_are_accepted_and_turns_are_stamped():
    svc = _servicer()

    async def go():
        now = await _epoch(svc)
        a = await _turn(svc, "打开空调", epoch=now)
        b = await _turn(svc, "调到26度")                     # 旧调用方：不带代际
        raw = await svc.store.get_session("s1", 10, user_id=UID, occupant_id="primary")
        return now, a, b, raw

    now, a, b, raw = asyncio.run(go())
    assert a.ok and b.ok
    assert [t["memory_epoch"] for t in raw] == [now, now]


def test_a_delete_waits_for_a_write_already_in_progress_and_then_removes_it():
    svc = _servicer()
    inside, release = asyncio.Event(), asyncio.Event()
    original = svc.store.append_turn

    async def slow_append(*args, **kwargs):
        inside.set()
        await release.wait()
        return await original(*args, **kwargs)

    svc.store.append_turn = slow_append

    async def go():
        now = await _epoch(svc)
        writing = asyncio.create_task(_turn(svc, "我家在云岚小区", epoch=now))
        await inside.wait()
        forgetting = asyncio.create_task(
            svc.ForgetUser(memory_pb2.ForgetUserRequest(user_id=UID), None))
        await asyncio.sleep(0.01)
        assert not forgetting.done()                         # 删除等写入完成，不与它交错
        release.set()
        await writing
        await forgetting
        return await _turns(svc)

    assert asyncio.run(go()) == []


def test_a_delete_during_extraction_drops_the_whole_batch():
    svc = _servicer()
    asked, release = asyncio.Event(), asyncio.Event()

    async def slow_llm(messages):
        asked.set()
        await release.wait()
        return PREF

    original = svc.store.consolidate
    svc.store.consolidate = lambda *a, **k: original(*a, complete_fn=slow_llm, **k)

    async def go():
        now = await _epoch(svc)
        await _turn(svc, "记住我喜欢空调26度", epoch=now)
        extracting = asyncio.create_task(svc._consolidate_bg("s1", UID, "primary", ""))
        await asyncio.wait_for(asked.wait(), 2)
        await svc.ForgetUser(memory_pb2.ForgetUserRequest(user_id=UID), None)
        release.set()
        await extracting
        return await _memories(svc)

    assert asyncio.run(go()) == []


def test_extraction_without_a_delete_still_writes():
    svc = _servicer()
    original = svc.store.consolidate

    async def llm(messages):
        return PREF

    svc.store.consolidate = lambda *a, **k: original(*a, complete_fn=llm, **k)

    async def go():
        await _turn(svc, "记住我喜欢空调26度", epoch=await _epoch(svc))
        await svc._consolidate_bg("s1", UID, "primary", "")
        return await _memories(svc)

    assert asyncio.run(go()) == ["用户喜欢空调26度"]


def test_a_deleted_item_is_not_extracted_again_from_turns_before_the_delete():
    svc = _servicer()
    prompts = []

    async def llm(messages):
        # 按窗口作答：窗口里有那句原话才抽得出那条偏好
        prompts.append(messages[-1]["content"])
        return PREF if "26度" in prompts[-1] else "[]"

    original = svc.store.consolidate
    svc.store.consolidate = lambda *a, **k: original(*a, complete_fn=llm, **k)

    async def go():
        await _turn(svc, "记住我喜欢空调26度", epoch=await _epoch(svc))
        await svc._consolidate_bg("s1", UID, "primary", "")
        item = (await svc.store.recall(UID, occupant_id="primary", query=""))[0][0]
        await svc.DeleteMemoryItem(memory_pb2.DeleteMemoryItemRequest(
            user_id=UID, occupant_id="primary", item_id=item["id"]), None)
        await _turn(svc, "导航去公司", epoch=await _epoch(svc))
        await svc._consolidate_bg("s1", UID, "primary", "")
        return await _memories(svc), await _turns(svc)

    memories, turns = asyncio.run(go())
    assert memories == []                                     # 没有从删除前的原话里长回来
    assert "记住我喜欢空调26度" in prompts[0]
    assert "记住我喜欢空调26度" not in prompts[1] and "导航去公司" in prompts[1]
    assert turns == ["记住我喜欢空调26度", "导航去公司"]          # 会话历史本身不动


def test_routines_and_suggestions_stop_after_a_delete():
    svc = _servicer()
    emitted = []

    async def routines(user_id, occupant_id):
        return [{"text": "每天八点去公司", "predicate": "routine.commute", "suggestion": "要导航去公司吗"}]

    async def emit(*args):
        emitted.append(args)

    svc.store.derive_routines = routines
    svc._emit_proactive = emit

    async def go():
        stale = await _epoch(svc)
        await svc.ForgetUser(memory_pb2.ForgetUserRequest(user_id=UID), None)
        try:
            await svc._derive_and_emit(UID, "primary", new_ids=[], epoch=stale)
        except StaleEpoch:
            return "refused"
        return "emitted"

    assert asyncio.run(go()) == "refused" and emitted == []


def test_a_batch_for_several_owners_cannot_claim_one_epoch():
    svc = _servicer()

    async def go():
        items = [memory_pb2.MemoryItem(user_id=uid, kind="semantic", text="t", predicate="p.x")
                 for uid in ("u-a", "u-b")]
        claimed = await svc.Remember(memory_pb2.RememberRequest(
            items=items, memory_epoch=await _epoch(svc, "u-a")), None)
        legacy = await svc.Remember(memory_pb2.RememberRequest(items=items), None)
        return claimed, legacy

    claimed, legacy = asyncio.run(go())
    assert (claimed.ok, claimed.error) == (False, STALE)
    assert legacy.ok and len(legacy.ids) == 2


def test_turn_eligibility_for_extraction():
    assert eligible("3.aa", "3.aa") and not eligible("2.bb", "3.aa")
    assert eligible("", "0.aa") and not eligible("", "1.aa")     # 修前的轮次只在没删过时有效
    assert generation("7.ab") == 7 and generation("") == -1 and generation("x.ab") == -1


class _Redis:
    """Only what OwnerFence uses."""

    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


def test_redis_backed_epoch_survives_restart_and_a_lost_key_refuses_old_writers():
    redis = _Redis()

    async def getter():
        return redis

    async def go():
        first = OwnerFence(getter)
        e0 = await first.current(UID)
        again = await OwnerFence(getter).current(UID)        # 进程重启：同一个值
        async with first.deleting(UID) as e1:
            pass
        del redis.data["mem_epoch:" + UID]                   # Redis 丢了这个键
        rebuilt = await first.current(UID)
        try:
            async with first.writing(UID, e1):
                return e0, again, e1, rebuilt, "written"
        except StaleEpoch:
            return e0, again, e1, rebuilt, "refused"

    e0, again, e1, rebuilt, outcome = asyncio.run(go())
    assert e0 == again and generation(e1) == 1
    assert generation(rebuilt) == 0 and rebuilt != e0
    assert outcome == "refused"


_ROOT = os.path.abspath(os.path.join(_MEM_DIR, ".."))
_WRITE_REQUESTS = ("AppendTurnRequest(", "RememberRequest(", "UpsertProfileRequest(")


def _production_sources():
    for top in ("agents", "orchestrator", "llm-gateway", "memory", "runtime", "security",
                "proactive", "payment-gateway", "registry", "scripts"):
        for base, dirs, files in os.walk(os.path.join(_ROOT, top)):
            dirs[:] = [d for d in dirs if d not in ("tests", "node_modules", "__pycache__")]
            for name in files:
                # Manual real-stack probes (scripts/probe_*.py) are test tools like the e2e
                # scripts under test/; the epoch probe replays stale epochs on purpose.
                if name.endswith(".py") and not name.startswith(("test_", "probe_")):
                    yield os.path.join(base, name)


def _calls(source, needle):
    at = source.find(needle)
    while at >= 0:
        depth, end = 0, at + len(needle) - 1
        while True:
            depth += {"(": 1, ")": -1}.get(source[end], 0)
            if depth == 0:
                break
            end += 1
        yield source[at:end + 1]
        at = source.find(needle, end)


def test_every_production_memory_write_carries_the_observed_epoch():
    # A writer that leaves memory_epoch out is a legacy writer: Memory cannot refuse what it
    # observed before a deletion. Tests and e2e seeding scripts stay legacy on purpose.
    found = {}
    for path in _production_sources():
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for needle in _WRITE_REQUESTS:
            for call in _calls(source, needle):
                found.setdefault(os.path.relpath(path, _ROOT).replace(os.sep, "/"), []).append(
                    "memory_epoch=" in call)
    assert found and all(all(flags) for flags in found.values()), found
    assert set(found) == {"agents/_sdk/clients.py", "orchestrator/cloud/clients.py",
                          "orchestrator/edge/server.py", "llm-gateway/s2s/reflux.py"}
