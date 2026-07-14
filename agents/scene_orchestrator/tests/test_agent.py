"""scene-orchestrator 契约测试：6 intent + 合并匹配 + 快照恢复。

v1 断言（list / activate / NEED_CONFIRM / payload 带 command）**必须继续绿**——
预置 4 场景零迁移是硬约束。deactivate 的断言按新语义更新（从嘴炮话术改为真恢复动作）。
"""
import asyncio
import json
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents._sdk.shared_state import SCENE_ACTIVE, SCENE_PENDING
from agents._sdk.testing import make_context, run_handle
from agents.scene_orchestrator.src import catalog as C
from agents.scene_orchestrator.src.agent import SceneOrchestratorAgent
from agents.scene_orchestrator.src.store import USER, Scene


# ── 夹具：内存 store + 可控 shared_state + mock LLM ─────────────────────────

class FakeLLM:
    def __init__(self, reply: str = ""):
        self.reply = reply
        self.calls = 0
        self.last_prompt = ""

    async def complete(self, messages, **kw):
        self.calls += 1
        self.last_prompt = " ".join(m.get("content", "") for m in messages)
        if not self.reply:
            raise RuntimeError("no LLM in this test")
        return self.reply


class KV:
    """把 shared_state 读写钉在内存里（make_context 的 AsyncMock 不会真存）。"""

    def __init__(self):
        self.data: dict = {}

    def bind(self, ctx):
        async def save(key, value):
            self.data[key] = value
            return True

        async def load(key):
            return self.data.get(key)

        ctx.save_shared_state = save
        ctx.load_shared_state = load
        return ctx


def _agent(llm_reply: str = "") -> SceneOrchestratorAgent:
    a = SceneOrchestratorAgent()
    a.llm = FakeLLM(llm_reply)
    asyncio.run(a.store.init())          # 空 DSN → 内存后端
    return a


def _ctx(kv: KV, **kw):
    return kv.bind(make_context(**kw))


def _run(coro):
    return asyncio.run(coro)


_FISHING = json.dumps({
    "name": "钓鱼模式", "description": "氛围灯10% + 外循环", "goal": "湖边钓鱼",
    "actions": [
        {"type": "vehicle.control", "command": "ambient_light.set",
         "params": {"brightness": "10", "color": "warm_white"}},
        {"type": "vehicle.control", "command": "hvac.set",
         "params": {"temperature": "22", "mode": "外循环"}},
    ],
    "unsupported": ["放舒缓音乐"],
}, ensure_ascii=False)


# ── v1 兼容（预置场景零迁移）────────────────────────────────────────────────

def test_list_scenes():
    res = _run(run_handle(_agent(), "scene.list", slots={},
                          raw_text="有哪些场景模式", ctx=_ctx(KV())))
    assert res.status == "ok"
    assert "回家" in res.speech or "露营" in res.speech


def test_activate_go_home():
    res = _run(run_handle(_agent(), "scene.activate", slots={"scene": "回家模式"},
                          raw_text="开启回家模式", ctx=_ctx(KV())))
    assert res.status == "ok" and "回家" in res.speech


def test_activate_camping_needs_confirm():
    res = _run(run_handle(_agent(), "scene.activate", slots={"scene": "露营模式"},
                          raw_text="露营模式", ctx=_ctx(KV())))
    assert res.status == "need_confirm"
    assert res.actions and res.actions[0].get("require_confirm") is True


def test_activate_nap_needs_confirm():
    res = _run(run_handle(_agent(), "scene.activate", slots={"scene": "午休"},
                          raw_text="午休模式", ctx=_ctx(KV())))
    assert res.status == "need_confirm"


def test_activate_action_payload_carries_command():
    """vehicle.control 动作必须把 command 并入 payload（VAL 经 payload['command'] 取指令）。"""
    res = _run(run_handle(_agent(), "scene.activate", slots={"scene": "回家模式"},
                          raw_text="开启回家模式", ctx=_ctx(KV())))
    vc = [a for a in res.actions if a["type"] == "vehicle.control"]
    assert vc
    for a in vc:
        assert a["payload"].get("command"), f"动作缺 command: {a}"
    res2 = _run(run_handle(_agent(), "scene.activate", slots={"scene": "浪漫模式"},
                           raw_text="浪漫模式", ctx=_ctx(KV())))
    frag = [a for a in res2.actions if a["payload"].get("command") == "fragrance.on"]
    assert frag and frag[0]["payload"], "fragrance.on 的 payload 不应为空（会被 Executor 丢弃）"


def test_activate_unknown_scene():
    res = _run(run_handle(_agent(), "scene.activate", slots={"scene": "蹦迪模式"},
                          raw_text="蹦迪模式", ctx=_ctx(KV())))
    assert res.status == "ok" and "没有找到" in res.speech


def test_activate_missing_scene_slot():
    res = _run(run_handle(_agent(), "scene.activate", slots={},
                          raw_text="", ctx=_ctx(KV())))
    assert res.status == "need_slot"


def test_unsupported_intent():
    res = _run(run_handle(_agent(), "scene.unknown", slots={}, raw_text="xxx",
                          ctx=_ctx(KV())))
    assert res.status == "failed"


def test_builtin_scenes_are_catalog_valid():
    """预置场景的每条动作都必须命中 VAL 词表——否则会被端侧静默丢弃（D3 漂移护栏）。"""
    a = _agent()
    for s in a._builtin:
        assert s.actions, f"{s.name} 没有动作"
        for act in s.actions:
            ok, _, reason = C.validate_action(act, a.catalog)
            assert ok, f"{s.name} 的动作不合词表：{act} → {reason}"


# ── 硬伤修复：状态位 / 快照 / 危险确认 ──────────────────────────────────────

def test_activate_appends_scene_mode():
    """硬伤 6：激活要写 scene_mode 状态位，车辆状态镜像才知道"当前在露营模式"。"""
    res = _run(run_handle(_agent(), "scene.activate", slots={"scene": "回家模式"},
                          raw_text="开启回家模式", ctx=_ctx(KV())))
    sm = [a for a in res.actions if a["payload"].get("command") == "scene_mode.set"]
    assert sm and sm[0]["payload"]["mode"] == "go_home"


def test_activate_writes_scene_active_with_generation_and_snapshot():
    kv = KV()
    a = _agent()
    a.mirror._state = {"hvac_temp": 21, "ambient_light_brightness": 80,
                       "ambient_light": True, "ambient_light_color": "blue"}
    _run(run_handle(a, "scene.activate", slots={"scene": "回家模式"},
                    raw_text="开启回家模式", ctx=_ctx(kv)))
    act = kv.data[SCENE_ACTIVE]
    assert act["scene_id"] == "go_home" and act["activation_id"]
    assert act["solved_actions"], "solved_actions 是恢复基准，不能空"
    assert act["snapshot"]["hvac_temp"] == 21          # 激活前的值
    assert act["snapshot"]["ambient_light_brightness"] == 80


def test_activate_confirmed_dispatches():
    """确认轮（meta.confirmed=true）真正下发动作——v1 这里会无限 NEED_CONFIRM 打转。"""
    res = _run(run_handle(_agent(), "scene.activate", slots={"scene": "露营模式"},
                          raw_text="确认", ctx=_ctx(KV()), meta={"confirmed": "true"}))
    assert res.status == "ok"
    cmds = [a["payload"].get("command") for a in res.actions]
    assert "seat.recline" in cmds and "scene_mode.set" in cmds


# ── deactivate 真恢复（D5）──────────────────────────────────────────────────

def test_deactivate_without_active_scene():
    res = _run(run_handle(_agent(), "scene.deactivate", slots={},
                          raw_text="退出场景", ctx=_ctx(KV())))
    assert res.status == "ok" and "没有开启" in res.speech


def test_deactivate_restores_from_snapshot():
    """退出要把车真的恢复回去——v1 只回一句「已退出XX」，座椅还躺着（硬伤 2）。"""
    kv = KV()
    a = _agent()
    a.mirror._state = {"hvac_temp": 21, "volume": 35, "ambient_light": False}
    _run(run_handle(a, "scene.activate", slots={"scene": "午休模式"},
                    raw_text="午休模式", ctx=_ctx(kv), meta={"confirmed": "true"}))
    res = _run(run_handle(a, "scene.deactivate", slots={}, raw_text="退出午休模式",
                          ctx=_ctx(kv), meta={"confirmed": "true"}))
    assert res.status == "ok"
    by_cmd = {x["payload"].get("command"): x["payload"] for x in res.actions}
    assert by_cmd["hvac.set"]["temperature"] == "21"       # 快照值，不是默认 24
    assert by_cmd["volume.set"]["level"] == "35"
    assert "ambient_light.close" in by_cmd                 # 激活前是关的 → 关回去
    assert by_cmd["seat.recline"]["angle"] == "90"         # 快照无 seat_recline → 复位默认
    assert by_cmd["scene_mode.set"]["mode"] == "off"
    assert kv.data[SCENE_ACTIVE] == {}                     # 清掉激活态


def test_deactivate_with_seat_needs_confirm():
    """D5：恢复动作含座椅（危险类）→ 照走 NEED_CONFIRM。"""
    kv = KV()
    a = _agent()
    _run(run_handle(a, "scene.activate", slots={"scene": "午休模式"}, raw_text="午休模式",
                    ctx=_ctx(kv), meta={"confirmed": "true"}))
    res = _run(run_handle(a, "scene.deactivate", slots={}, raw_text="退出",
                          ctx=_ctx(kv)))
    assert res.status == "need_confirm" and "座椅" in res.speech


def test_deactivate_restores_only_what_was_dispatched():
    """v2.1 修正④：恢复基准是本次实际下发集（solved_actions），不是场景原始 actions。"""
    kv = KV()
    a = _agent()
    _run(run_handle(a, "scene.activate", slots={"scene": "浪漫模式"}, raw_text="浪漫模式",
                    ctx=_ctx(kv)))
    kv.data[SCENE_ACTIVE]["solved_actions"] = [        # 模拟只下发了氛围灯（其余被裁剪）
        {"type": "vehicle.control", "command": "ambient_light.set",
         "params": {"brightness": "40"}, "require_confirm": False}]
    res = _run(run_handle(a, "scene.deactivate", slots={}, raw_text="退出",
                          ctx=_ctx(kv), meta={"confirmed": "true"}))
    cmds = [x["payload"].get("command") for x in res.actions]
    assert "fragrance.close" not in cmds, "没下发过的动作不该被恢复（会覆盖用户手动调整）"
    assert "scene_mode.set" in cmds


# ── scene.create（编译闭环）─────────────────────────────────────────────────

def test_create_readback_then_confirm_persists():
    kv, a = KV(), _agent(_FISHING)
    res = _run(run_handle(a, "scene.create", slots={},
                          raw_text="帮我创建一个钓鱼模式：氛围灯调到10%，空调外循环",
                          ctx=_ctx(kv)))
    assert res.status == "need_confirm"
    assert "钓鱼模式" in res.speech and "保存吗" in res.speech
    assert "放舒缓音乐" in res.speech                    # 做不到的诉求诚实告知
    assert res.ui_card["type"] == "scene_card" and len(res.ui_card["actions_preview"]) == 2
    assert kv.data[SCENE_PENDING]["draft"]["actions"]

    res2 = _run(run_handle(a, "scene.create", slots={}, raw_text="确认",
                           ctx=_ctx(kv), meta={"confirmed": "true"}))
    assert res2.status == "ok" and "开启钓鱼模式" in res2.speech
    assert a.llm.calls == 1, "确认轮不该重跑 LLM（会产出与用户确认时不一样的动作）"
    saved = _run(a.store.get_by_name("u1", "钓鱼模式"))
    assert saved and len(saved.actions) == 2


def test_create_missing_spec_asks_then_continues():
    """只说名字 → NEED_SLOT + 写 pending；下一轮补内容续接（名字不丢）。"""
    kv, a = KV(), _agent(_FISHING)
    res = _run(run_handle(a, "scene.create", slots={}, raw_text="帮我建个钓鱼模式",
                          ctx=_ctx(kv)))
    assert res.status == "need_slot" and res.missing_slots == ["spec"]
    assert kv.data[SCENE_PENDING]["name"] == "钓鱼模式"

    # 续接轮：engine 会把用户新话填进 spec 槽，并（对挂起步）注入 confirmed=true
    res2 = _run(run_handle(a, "scene.create", slots={"spec": "氛围灯调到10%，空调外循环"},
                           raw_text="氛围灯调到10%，空调外循环", ctx=_ctx(kv),
                           meta={"confirmed": "true"}))
    assert res2.status == "need_confirm", "pending 里没 draft 时，confirmed 不等于「确认草案」"
    assert "钓鱼模式" in res2.speech


def test_create_rejects_edge_mode_name():
    """D8：端侧模式词（运动/省电…）不能拿来造场景，否则会劫持端侧毫秒级秒回。"""
    res = _run(run_handle(_agent(_FISHING), "scene.create", slots={},
                          raw_text="创建一个运动模式：座椅放平", ctx=_ctx(KV())))
    # 面向用户的拒绝用 OK：聚合器对 FAILED 只取 error 码，speech 会被丢成「抱歉，处理失败」
    assert res.status == "ok" and "本来就有" in res.speech


def test_create_all_actions_dropped_fails_honestly():
    raw = json.dumps({"name": "蹦迪模式",
                      "actions": [{"type": "vehicle.control", "command": "disco.on"}]},
                     ensure_ascii=False)
    kv = KV()
    res = _run(run_handle(_agent(raw), "scene.create", slots={},
                          raw_text="创建蹦迪模式：打开迪斯科球", ctx=_ctx(kv)))
    assert res.status == "ok" and "建不了" in res.speech
    assert not kv.data.get(SCENE_PENDING, {}).get("draft"), "建不了就不该留草案"


# ── 用户场景遮蔽预置（D4）──────────────────────────────────────────────────

def test_user_scene_shadows_builtin():
    kv, a = KV(), _agent()
    _run(a.store.save(Scene(user_id="u1", name="露营模式", description="只开灯",
                            actions=[{"type": "vehicle.control",
                                      "command": "ambient_light.set",
                                      "params": {"brightness": "20"},
                                      "require_confirm": False}])))
    res = _run(run_handle(a, "scene.activate", slots={"scene": "露营模式"},
                          raw_text="开启露营模式", ctx=_ctx(kv)))
    assert res.status == "ok", "用户版没有座椅动作 → 不该要确认（说明命中的是预置版）"
    cmds = [x["payload"].get("command") for x in res.actions]
    assert "seat.recline" not in cmds and "ambient_light.set" in cmds
    assert [x["payload"]["mode"] for x in res.actions
            if x["payload"].get("command") == "scene_mode.set"] == ["露营模式"]


def test_list_splits_mine_and_builtin():
    kv, a = KV(), _agent()
    _run(a.store.save(Scene(user_id="u1", name="钓鱼模式",
                            actions=[{"type": "vehicle.control",
                                      "command": "fragrance.on", "params": {}}])))
    res = _run(run_handle(a, "scene.list", slots={}, raw_text="有哪些场景", ctx=_ctx(kv)))
    assert "你建的" in res.speech and "钓鱼模式" in res.speech
    card = res.ui_card
    assert card["type"] == "scene_list"
    assert [x["name"] for x in card["mine"]] == ["钓鱼模式"]
    assert len(card["builtin"]) == 4


# ── scene.update / delete ───────────────────────────────────────────────────

def test_update_param_level_is_deterministic():
    """「把钓鱼模式的温度改成24」——参数级改动不惊动 LLM。"""
    kv, a = KV(), _agent()          # 无 LLM：调到了就说明走的确定性路径
    _run(a.store.save(Scene(user_id="u1", name="钓鱼模式",
                            actions=[{"type": "vehicle.control", "command": "hvac.set",
                                      "params": {"temperature": "22"},
                                      "require_confirm": False}])))
    res = _run(run_handle(a, "scene.update", slots={"scene": "钓鱼模式"},
                          raw_text="把钓鱼模式的温度改成24", ctx=_ctx(kv)))
    assert res.status == "ok" and "24" in res.speech
    assert _run(a.store.get_by_name("u1", "钓鱼模式")).actions[0]["params"]["temperature"] == "24"
    assert a.llm.calls == 0


def test_update_builtin_guides_to_copy():
    res = _run(run_handle(_agent(), "scene.update", slots={"scene": "露营模式"},
                          raw_text="把露营模式的温度改成26", ctx=_ctx(KV())))
    assert res.status == "ok" and "内置场景" in res.speech and "创建" in res.speech


def test_delete_needs_confirm_then_deletes():
    kv, a = KV(), _agent()
    _run(a.store.save(Scene(user_id="u1", name="钓鱼模式",
                            actions=[{"type": "vehicle.control",
                                      "command": "fragrance.on", "params": {}}])))
    res = _run(run_handle(a, "scene.delete", slots={"scene": "钓鱼模式"},
                          raw_text="删掉钓鱼模式", ctx=_ctx(kv)))
    assert res.status == "need_confirm"
    res2 = _run(run_handle(a, "scene.delete", slots={"scene": "钓鱼模式"},
                           raw_text="确认", ctx=_ctx(kv), meta={"confirmed": "true"}))
    assert res2.status == "ok"
    assert _run(a.store.get_by_name("u1", "钓鱼模式")) is None


def test_delete_builtin_only_disables():
    """预置场景随镜像发版，删不掉——只从列表里隐藏。"""
    kv, a = KV(), _agent()
    _run(run_handle(a, "scene.delete", slots={"scene": "浪漫模式"}, raw_text="删掉浪漫模式",
                    ctx=_ctx(kv), meta={"confirmed": "true"}))
    res = _run(run_handle(a, "scene.list", slots={}, raw_text="有哪些场景", ctx=_ctx(kv)))
    assert "浪漫模式" not in res.speech


def test_honest_refusals_use_ok_status():
    """聚合器对 FAILED 只取 error 码拼「抱歉，处理失败」，会把诚实话术整个丢掉
    （aggregator.py:119-121）——所以面向用户的拒绝必须用 OK 状态。"""
    kv, a = KV(), _agent()
    for intent, slots, raw in (
        ("scene.update", {"scene": "不存在模式"}, "把不存在模式的温度改成24"),
        ("scene.delete", {"scene": "不存在模式"}, "删掉不存在模式"),
        ("scene.activate", {"scene": "不存在模式"}, "开启不存在模式"),
    ):
        res = _run(run_handle(a, intent, slots=slots, raw_text=raw, ctx=_ctx(kv)))
        assert res.status == "ok", f"{intent} 的诚实拒绝不该用 FAILED（话术会被吞）"
        assert "没找到" in res.speech or "没有找到" in res.speech


# ── P1：custom_params 参数覆盖 ─────────────────────────────────────────────

def test_activate_custom_params_override():
    """「开启午休模式，温度26」→ 确定性覆盖同对象动作（26 而非场景里的 24），不惊动 LLM。"""
    kv, a = KV(), _agent()          # 无 LLM：能覆盖就说明走的确定性路径
    res = _run(run_handle(a, "scene.activate", slots={"scene": "开启午休模式，温度26"},
                          raw_text="开启午休模式，温度26", ctx=_ctx(kv),
                          meta={"confirmed": "true"}))
    assert res.status == "ok"
    hvac = [x["payload"] for x in res.actions
            if x["payload"].get("command") == "hvac.set"]
    assert hvac and hvac[0]["temperature"] == "26"
    assert a.llm.calls == 0


def test_activate_custom_params_survive_confirm_round():
    """确认轮的 raw_text 是「确认」——数值只在 slots 里（route_hint 灌的原句）。
    只看 raw_text 会把覆盖弄丢，用户确认后拿到的是 24 不是 26。"""
    kv, a = KV(), _agent()
    res = _run(run_handle(a, "scene.activate", slots={"scene": "开启午休模式，温度26"},
                          raw_text="确认", ctx=_ctx(kv), meta={"confirmed": "true"}))
    hvac = [x["payload"] for x in res.actions
            if x["payload"].get("command") == "hvac.set"]
    assert hvac and hvac[0]["temperature"] == "26"


def test_activate_without_numbers_unchanged():
    kv, a = KV(), _agent()
    res = _run(run_handle(a, "scene.activate", slots={"scene": "午休模式"},
                          raw_text="开启午休模式", ctx=_ctx(kv), meta={"confirmed": "true"}))
    hvac = [x["payload"] for x in res.actions
            if x["payload"].get("command") == "hvac.set"]
    assert hvac and hvac[0]["temperature"] == "24"          # 场景原值


# ── P1：会话沉淀（D11 桥）─────────────────────────────────────────────────

_SEDIMENT_DRAFT = json.dumps({
    "name": "加班模式", "description": "空调28度 + 氛围灯45%", "goal": "晚上在车里加班",
    "actions": [
        {"type": "vehicle.control", "command": "hvac.set", "params": {"temperature": "28"}},
        {"type": "vehicle.control", "command": "ambient_light.set",
         "params": {"brightness": "45"}},
    ],
}, ensure_ascii=False)


def test_create_from_history_sediment():
    """「把刚才这些存成加班模式」——内容不在这句话里，在最近做过的操作里（D11 沉淀桥）。"""
    kv, a = KV(), _agent(_SEDIMENT_DRAFT)
    ctx = _ctx(kv, history=[
        {"role": "user", "text": "把空调调到28度", "ts": 1},
        {"role": "assistant", "text": "已为您打开空调，设定28度", "ts": 2},
        {"role": "user", "text": "氛围灯调到45%", "ts": 3},
        {"role": "assistant", "text": "氛围灯已调整", "ts": 4},
    ])
    res = _run(run_handle(a, "scene.create", slots={},
                          raw_text="把刚才这些存成加班模式", ctx=ctx))
    assert res.status == "need_confirm" and "加班模式" in res.speech
    # 编译器收到的必须是 history 拼出来的操作，而不是「把刚才这些存成加班模式」这句元指令
    # ——否则 LLM 只能瞎猜（mock LLM 会把这个 bug 盖住，故直接断言 prompt 内容）
    assert "空调调到28度" in a.llm.last_prompt and "氛围灯调到45%" in a.llm.last_prompt
    assert "把刚才这些存成" not in a.llm.last_prompt.split("用户说")[-1]
    assert a.llm.calls == 1
    res2 = _run(run_handle(a, "scene.create", slots={}, raw_text="确认",
                           ctx=_ctx(kv), meta={"confirmed": "true"}))
    assert res2.status == "ok"
    saved = _run(a.store.get_by_name("u1", "加班模式"))
    assert saved and len(saved.actions) == 2


def test_sediment_without_history_is_honest():
    """没有可沉淀的操作 → 诚实说没找到，别凭空编一个场景。"""
    kv, a = KV(), _agent(_SEDIMENT_DRAFT)
    res = _run(run_handle(a, "scene.create", slots={},
                          raw_text="把刚才这些存成加班模式", ctx=_ctx(kv, history=[])))
    assert res.status == "ok" and "没找到刚才做过" in res.speech
    assert a.llm.calls == 0, "没内容就别调 LLM"


# ── P2：策略引擎接线（Ground·Solve + Verify）───────────────────────────────

_POLICY_DRAFT = json.dumps({
    "name": "观星模式", "description": "灯关 + 座椅放平 + 空调随温度",
    "goal": "野外看星星",
    "guards": [{"key": "gear", "op": "eq", "value": "P", "mode": "confirm",
                "message": "最好停好车"},
               {"key": "moon_phase", "op": "eq", "value": "full"}],     # 幻觉键 → 剔除
    "actions": [
        {"type": "vehicle.control", "command": "ambient_light.close", "params": {}},
        {"type": "vehicle.control", "command": "seat.recline",
         "params": {"position": "front_left", "angle": "170"}, "on_fail": "defer_p"},
        {"type": "vehicle.control", "command": "hvac.set", "params": {"temperature": "22"},
         "when": {"key": "cabin_temp", "op": "gte", "value": 28}},
        {"type": "vehicle.control", "command": "hvac.set", "params": {"temperature": "26"},
         "when": {"key": "cabin_temp", "op": "lt", "value": 15}},
        {"type": "vehicle.control", "command": "volume.set", "params": {"level": "0"},
         "when": {"key": "cosmic_ray", "op": "lt", "value": 3}},        # 幻觉键 → 条件剔除
    ],
}, ensure_ascii=False)


def test_compile_policy_fields_with_hallucinated_keys_dropped():
    """幻觉条件键会让条件永真（分支形同虚设）或永假（动作凭空消失）——剔除并告知。"""
    kv, a = KV(), _agent(_POLICY_DRAFT)
    res = _run(run_handle(a, "scene.create", slots={},
                          raw_text="创建观星模式：灯关掉、座椅放倒、空调随温度", ctx=_ctx(kv)))
    assert res.status == "need_confirm"
    draft = kv.data[SCENE_PENDING]["draft"]
    assert [g["key"] for g in draft["guards"]] == ["gear"]      # moon_phase 被剔
    whens = [act.get("when", {}).get("key") for act in draft["actions"]]
    assert whens == [None, None, "cabin_temp", "cabin_temp", None]   # cosmic_ray 条件被剔
    assert draft["actions"][4]["command"] == "volume.set"       # 但动作保留（条件降级为无条件）
    assert [act.get("on_fail") for act in draft["actions"]][1] == "defer_p"


def _policy_scene():
    return Scene(user_id="u1", name="观星模式", source=USER,
                 guards=[{"key": "battery", "op": "gte", "value": 20, "mode": "block",
                          "message": "电量太低"}],
                 actions=[
                     {"type": "vehicle.control", "command": "ambient_light.close",
                      "params": {}, "require_confirm": False},
                     {"type": "vehicle.control", "command": "hvac.set",
                      "params": {"temperature": "22"}, "require_confirm": False,
                      "when": {"key": "cabin_temp", "op": "gte", "value": 28}},
                     {"type": "vehicle.control", "command": "hvac.set",
                      "params": {"temperature": "26"}, "require_confirm": False,
                      "when": {"key": "cabin_temp", "op": "lt", "value": 15}},
                 ])


def test_activate_env_branch_hot():
    kv, a = KV(), _agent()
    _run(a.store.save(_policy_scene()))
    a.mirror._state = {"battery": 80, "cabin_temp": 35}
    res = _run(run_handle(a, "scene.activate", slots={"scene": "观星模式"},
                          raw_text="开启观星模式", ctx=_ctx(kv)))
    temps = [x["payload"]["temperature"] for x in res.actions
             if x["payload"].get("command") == "hvac.set"]
    assert temps == ["22"], "35℃ 应只走制冷分支"


def test_activate_env_branch_missing_data_does_not_double_fire():
    """v2.1 修正②真实链路复现：车内温度读不到 → 两条互斥分支都跳过，并诚实告知。"""
    kv, a = KV(), _agent()
    _run(a.store.save(_policy_scene()))
    a.mirror._state = {"battery": 80}                # 无 cabin_temp
    res = _run(run_handle(a, "scene.activate", slots={"scene": "观星模式"},
                          raw_text="开启观星模式", ctx=_ctx(kv)))
    cmds = [x["payload"].get("command") for x in res.actions]
    assert "hvac.set" not in cmds, "缺数据时互斥分支绝不能双发"
    assert "cabin_temp" in res.speech or "跳过" in res.speech


def test_activate_guard_block_rejects_honestly():
    kv, a = KV(), _agent()
    _run(a.store.save(_policy_scene()))
    a.mirror._state = {"battery": 5, "cabin_temp": 30}
    res = _run(run_handle(a, "scene.activate", slots={"scene": "观星模式"},
                          raw_text="开启观星模式", ctx=_ctx(kv)))
    assert res.status == "rejected" and "电量太低" in res.speech
    assert not res.actions


def test_activate_idempotent_all_done():
    """重复激活：都已是这个状态 → 诚实反馈，不空发一堆动作。"""
    kv, a = KV(), _agent()
    _run(a.store.save(Scene(user_id="u1", name="静音模式2", source=USER, actions=[
        {"type": "vehicle.control", "command": "volume.set", "params": {"level": "0"},
         "require_confirm": False}])))
    a.mirror._state = {"volume": 0}
    res = _run(run_handle(a, "scene.activate", slots={"scene": "静音模式2"},
                          raw_text="开启静音模式2", ctx=_ctx(kv)))
    assert res.status == "ok" and "无需调整" in res.speech and not res.actions


def test_activate_schedules_verify_and_deactivate_cancels():
    """激活注册后台对账、退出掐掉在飞任务（v2.1 修正③单飞）。

    断言必须在**同一个事件循环内**做——asyncio.run 退出时会取消挂起 task 并触发
    done_callback，`_tasks` 会被清空，跨 run 断言只会看到空字典。
    """
    kv, a = KV(), _agent()
    a.mirror._state = {"hvac_temp": 21}

    async def go():
        await run_handle(a, "scene.activate", slots={"scene": "回家模式"},
                         raw_text="开启回家模式", ctx=_ctx(kv))
        assert "u1" in a.verify._tasks, "激活应注册后台对账"
        await run_handle(a, "scene.deactivate", slots={}, raw_text="退出",
                         ctx=_ctx(kv), meta={"confirmed": "true"})
        assert "u1" not in a.verify._tasks, "退出应掐掉在飞对账"

    _run(go())


def test_ground_hour_uses_business_tz():
    """`when: {key:"hour"}` 的环境值必须按业务时区（UTC+8）取——容器本地时是 UTC，
    2026-07-14 评审修复前用 time.localtime()，「晚上10点后才调暗灯」这类条件错 8 小时。"""
    from datetime import datetime
    from agents.scene_orchestrator.src.triggers import BUSINESS_TZ

    a = _agent()
    before = datetime.now(BUSINESS_TZ).hour
    env = a._ground({})
    after = datetime.now(BUSINESS_TZ).hour
    assert env["hour"] in {before, after}          # 夹逼，容忍跨小时边界的瞬间
