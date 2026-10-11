"""香氛开关现状查询 `fragrance.query`（2026-10-11，核心旅程 P3 C02）。

修前：「打开香氛」之后问「香氛现在开着吗」，端侧把它认成 `open` 再被问句闸拦下上云，规划器判出「查询香氛当前是否开启」
却没有任何可派的步，落到闲聊——闲聊手里没有车态，答「我看不了当前状态」（另一趟答「开着呢」是按上文猜的）。
五段链路逐段钉住：原话 → 意图 → 对象 / 操作 → VAL（只读、不过写操作门控、读不到如实说）→ 话术；
经端侧服务时，只读查询不回传动作（动作 = 对车做了什么，审计 / 账本 / 旅程判定都按这个读）。
"""
import asyncio

from cockpit.orchestrator.v1 import orchestrator_pb2
from fast_intent import classify, classify_structured, is_local
from server import EdgeOrchestratorServicer
from val import VAL


def _query(v: VAL):
    return v.execute({"domain": "query", "intent": "query",
                      "data": {"object": "fragrance", "operate": "query"}})


# ── 原话 → 意图 ────────────────────────────────────────────────────────────

def test_state_question_is_a_local_read_not_an_open():
    s = classify_structured("香氛现在开着吗")
    assert s is not None
    assert (s["intent"], s["data"]["operate"], s["data"]["object"]) == ("query", "query", "fragrance")
    legacy = classify("香氛现在开着吗")
    assert legacy["name"] == "fragrance.query"
    assert is_local("fragrance.query")


def test_lookup_shell_no_longer_turns_the_fragrance_on():
    """修前「帮我看看香氛开着吗」带「帮我」过不了问句闸，被端侧执行成打开香氛。"""
    s = classify_structured("帮我看看香氛开着吗")
    assert s is not None and s["data"]["operate"] == "query"


def test_commands_still_open_and_close():
    assert classify("打开香氛")["name"] == "fragrance.on"
    assert classify("关闭香氛")["name"] == "fragrance.off"
    assert classify("让香氛开着")["name"] == "fragrance.on"


# ── VAL：只读、读车态、不编 ────────────────────────────────────────────────

def test_query_reads_the_state_without_changing_it():
    v = VAL()
    before = dict(v.state)
    ok, speech = _query(v)
    assert ok is True
    assert speech.startswith("香氛") and "关着" in speech and "开着" not in speech
    assert v.state == before


def test_query_after_open_says_on_with_the_level():
    v = VAL()
    v.execute({"domain": "setting", "intent": "control",
               "data": {"object": "fragrance", "operate": "set", "value": "3"}})
    ok, speech = _query(v)
    assert ok is True and "开着" in speech and "3档" in speech and "关着" not in speech


def test_query_names_the_simulated_source():
    ok, speech = _query(VAL())
    assert ok is True and speech.endswith("（模拟车读数）")


def test_query_without_a_reading_does_not_guess():
    v = VAL()
    v.state.pop("fragrance", None)
    ok, speech = _query(v)
    assert ok is True and "读不到" in speech
    assert "开着" not in speech and "关着" not in speech


def test_write_gates_do_not_block_a_state_question():
    """低电量禁高耗电、倒挡禁车控管的都是「做」；问现状照样答，打开照样拦。"""
    v = VAL()
    v.state["battery"] = 5
    assert _query(v)[0] is True
    ok, speech = v.execute({"domain": "setting", "intent": "control",
                            "data": {"object": "fragrance", "operate": "open"}})
    assert ok is False and "电量过低" in speech
    v.state["battery"] = 80
    v.state["gear"] = "R"
    assert _query(v)[0] is True


def test_query_response_key_exists():
    v = VAL()
    key = v._build_response_key("fragrance", "query", {})
    assert key in v.responses and "fragrance_query_unavailable" in v.responses


def test_capability_is_declared_read_only():
    from capabilities import _effect_for

    objects = (VAL().commands or {}).get("objects") or {}
    assert "query" in objects["fragrance"]["operates"]
    assert "fragrance.query" in objects["fragrance"]["edge_intents"]
    assert _effect_for("fragrance.query", objects) == "read"
    assert _effect_for("fragrance.on", objects) == "write"


# ── 端侧服务：只读查询不回传动作 ───────────────────────────────────────────

def _service(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()
    turns = []

    async def fake_turn(trace_id, session_id, **kwargs):
        turns.append({"trace_id": trace_id, "session_id": session_id, **kwargs})

    async def fake_span(*args, **kwargs):
        pass

    async def fake_memory(*args, **kwargs):
        return None

    service.obs.emit_turn = fake_turn
    service.obs.emit_span = fake_span
    service.memory.append = fake_memory

    async def cloud_stub(_request):
        # 本文件只验端侧本地路径。若退化成上云（例如认不出状态问句），立即给一个桩结果让断言当场变红，
        # 而不是挂在一个连不上的云端连接上（测试环境里那条 gRPC 调用没有截止时间）。
        yield orchestrator_pb2.HandleEvent(final=orchestrator_pb2.FinalResult(speech="〔云端桩〕"))

    service.cloud.handle = cloud_stub
    return service, turns


def _handle(service, text, session):
    request = orchestrator_pb2.HandleRequest(
        text=text, session_id=session, meta={"trace_id": f"{session}-trace"})
    finals = []

    async def run():
        async for event in service.Handle(request, None):
            if event.HasField("final"):
                finals.append(event.final)

    asyncio.run(run())
    return finals


def test_state_question_answers_locally_without_an_action(monkeypatch):
    service, turns = _service(monkeypatch)
    before = dict(service.val.state)
    finals = _handle(service, "香氛现在开着吗", "frag-query")
    assert len(finals) == 1
    assert "香氛" in finals[0].speech and "关着" in finals[0].speech
    assert list(finals[0].actions) == []                 # 只读：不是「对车做了什么」
    assert turns[0]["path"] == "local" and turns[0]["intents"] == ["fragrance.query"]
    assert service.val.state == before


def test_open_still_reports_its_action(monkeypatch):
    service, _turns = _service(monkeypatch)
    finals = _handle(service, "打开香氛", "frag-open")
    assert [dict(a.payload)["command"] for a in finals[0].actions] == ["fragrance.on"]
    assert service.val.state["fragrance"] is True


def test_battery_query_turn_is_actionless_too(monkeypatch):
    """同一条规则覆盖既有的电量查询：测试与云端上下文一直按「无动作的一轮」建模它。"""
    service, turns = _service(monkeypatch)
    finals = _handle(service, "电量还有多少", "battery-query")
    assert list(finals[0].actions) == []
    assert turns[0]["intents"] == ["battery.query"]


def test_mixed_local_turn_keeps_the_query_in_its_intents(monkeypatch):
    """全本地多意图：查询执行了但不算动作，本轮 intents 仍要记着它（obs / badcase 按 intents 排查）。"""
    service, turns = _service(monkeypatch)
    finals = _handle(service, "打开香氛，电量还有多少", "frag-multi")
    commands = [dict(a.payload)["command"] for a in finals[0].actions]
    assert commands == ["fragrance.on"]
    assert "battery.query" in turns[0]["intents"] and "fragrance.on" in turns[0]["intents"]
