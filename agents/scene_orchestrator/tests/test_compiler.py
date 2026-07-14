"""编译器单测（LLM 客户端 mock 注入；校验是确定性的，LLM 说了不算）。"""
import asyncio
import json
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.scene_orchestrator.src import compiler as CP
from agents.scene_orchestrator.src.catalog import load_catalog


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


class FakeLLM:
    """按脚本吐字符串；记录调用次数（重试断言用）。"""

    def __init__(self, *replies: str):
        self._replies = list(replies)
        self.calls = 0

    async def complete(self, messages, **kw):
        self.calls += 1
        if not self._replies:
            raise RuntimeError("LLM boom")
        return self._replies.pop(0)


def _run(coro):
    return asyncio.run(coro)


_GOOD = json.dumps({
    "name": "钓鱼模式", "description": "座椅放平 + 外循环 + 氛围灯10%",
    "goal": "在湖边车里舒服地钓鱼",
    "actions": [
        {"type": "vehicle.control", "command": "seat.recline",
         "params": {"position": "front_left", "angle": "160"}, "require_confirm": False},
        {"type": "vehicle.control", "command": "hvac.set",
         "params": {"temperature": "22", "mode": "外循环"}},
        {"type": "vehicle.control", "command": "ambient_light.set",
         "params": {"brightness": "10", "color": "warm_white"}},
    ],
    "unsupported": ["放舒缓音乐"],
}, ensure_ascii=False)


# ── 名字/内容抽取 ────────────────────────────────────────────────────────────

def test_extract_scene_name():
    assert CP.extract_scene_name("帮我创建一个钓鱼模式：座椅放平") == "钓鱼模式"
    assert CP.extract_scene_name("新建观星模式") == "观星模式"
    assert CP.extract_scene_name("开启午休模式") == "午休模式"
    assert CP.extract_scene_name("来个露营模式") == "露营模式"
    assert CP.extract_scene_name("退出钓鱼模式") == "钓鱼模式"
    assert CP.extract_scene_name("今天天气怎么样") == ""


def test_extract_spec():
    assert CP.extract_spec("帮我建个钓鱼模式：座椅放平，开外循环") == "座椅放平，开外循环"


# ── 正常编译 ────────────────────────────────────────────────────────────────

def test_compile_ok(cat):
    llm = FakeLLM(_GOOD)
    d = _run(CP.compile_scene(llm, cat, "帮我建个钓鱼模式：座椅放平、开外循环、氛围灯10%"))
    assert d.ok and d.name == "钓鱼模式" and len(d.actions) == 3
    assert d.goal and d.description
    assert llm.calls == 1


def test_unsupported_kept_as_honest_notice(cat):
    """做不到的诉求必须留痕告知，不静默丢（P0 不支持媒体）。"""
    d = _run(CP.compile_scene(FakeLLM(_GOOD), cat, "建个钓鱼模式"))
    assert any("放舒缓音乐" in x for x in d.dropped)


def test_danger_require_confirm_forced_over_llm(cat):
    """LLM 声明 seat.recline require_confirm=false —— 校验层强制改 true（§8.1）。"""
    d = _run(CP.compile_scene(FakeLLM(_GOOD), cat, "建个钓鱼模式"))
    seat = [a for a in d.actions if a["command"] == "seat.recline"]
    assert seat and seat[0]["require_confirm"] is True


def test_hallucinated_command_dropped(cat):
    raw = json.dumps({"name": "按摩模式", "actions": [
        {"type": "vehicle.control", "command": "massage.on", "params": {}},
        {"type": "vehicle.control", "command": "hvac.set", "params": {"temperature": "24"}},
    ]}, ensure_ascii=False)
    d = _run(CP.compile_scene(FakeLLM(raw), cat, "建个按摩模式"))
    assert d.ok and len(d.actions) == 1
    assert any("massage" in x for x in d.dropped)


def test_all_actions_dropped_is_not_saveable(cat):
    """全部动作被剔 → 不存空场景，诚实 FAILED（设计 §5.1②）。"""
    raw = json.dumps({"name": "蹦迪模式", "actions": [
        {"type": "vehicle.control", "command": "disco_ball.on", "params": {}}]},
        ensure_ascii=False)
    d = _run(CP.compile_scene(FakeLLM(raw), cat, "建个蹦迪模式"))
    assert not d.ok and d.error and "建不了" in d.error


def test_markdown_fence_tolerated(cat):
    d = _run(CP.compile_scene(FakeLLM(f"```json\n{_GOOD}\n```"), cat, "建个钓鱼模式"))
    assert d.ok and len(d.actions) == 3


def test_retry_once_then_succeed(cat):
    llm = FakeLLM("这不是 JSON", _GOOD)
    d = _run(CP.compile_scene(llm, cat, "建个钓鱼模式"))
    assert d.ok and llm.calls == 2


def test_two_failures_degrade_honestly(cat):
    llm = FakeLLM("噪声", "还是噪声")
    d = _run(CP.compile_scene(llm, cat, "建个钓鱼模式"))
    assert not d.ok and d.error and not d.actions
    assert llm.calls == 2


def test_llm_exception_degrades(cat):
    d = _run(CP.compile_scene(FakeLLM(), cat, "建个钓鱼模式"))
    assert not d.ok and d.error


def test_name_hint_wins(cat):
    """两轮续接：第一轮已定名，第二轮补内容时不许 LLM 改名。"""
    d = _run(CP.compile_scene(FakeLLM(_GOOD), cat, "座椅放平，开外循环",
                              name_hint="观星模式"))
    assert d.ok and d.name == "观星模式"


def test_out_of_range_clamped_with_note(cat):
    raw = json.dumps({"name": "桑拿模式", "actions": [
        {"type": "vehicle.control", "command": "hvac.set", "params": {"temperature": "50"}}]},
        ensure_ascii=False)
    d = _run(CP.compile_scene(FakeLLM(raw), cat, "建个桑拿模式"))
    assert d.ok and d.actions[0]["params"]["temperature"] == "32"
    assert d.notes and "32" in d.notes[0]


def test_draft_roundtrip():
    """Draft 要能存进 SCENE_PENDING 再取回（确认轮不重跑 LLM）。"""
    d = CP.Draft(name="钓鱼模式", description="x", goal="g",
                 actions=[{"type": "vehicle.control", "command": "fragrance.on",
                           "params": {}, "require_confirm": False}],
                 dropped=["「放歌」我还做不到"], ok=True)
    d2 = CP.Draft.from_dict(json.loads(json.dumps(d.to_dict(), ensure_ascii=False)))
    assert d2.ok and d2.name == "钓鱼模式" and d2.actions == d.actions
    assert d2.dropped == d.dropped


# ── 动作描述 ────────────────────────────────────────────────────────────────

def test_action_desc():
    assert "22" in CP.action_desc(
        {"type": "vehicle.control", "command": "hvac.set", "params": {"temperature": "22"}})
    assert "座椅" in CP.action_desc(
        {"type": "vehicle.control", "command": "seat.recline", "params": {"angle": "160"}})
    assert "家" in CP.action_desc({"type": "navigate", "payload": {"destination": "家"}})
    # 模板缺失也不抛错
    assert CP.action_desc(
        {"type": "vehicle.control", "command": "wiper.open", "params": {}})


def test_actions_preview_marks_danger():
    prev = CP.actions_preview([
        {"type": "vehicle.control", "command": "seat.recline",
         "params": {"angle": "160"}, "require_confirm": True},
        {"type": "vehicle.control", "command": "fragrance.on",
         "params": {}, "require_confirm": False}])
    assert prev[0]["danger"] is True and prev[1]["danger"] is False


def test_action_desc_never_renders_empty_slot():
    """参数名不匹配模板时别渲染成「氛围灯%」——退成可读的兜底描述。"""
    d = CP.action_desc({"type": "vehicle.control", "command": "ambient_light.set",
                        "params": {"level": "20"}})
    assert "20" in d and "氛围灯%" != d
    d2 = CP.action_desc({"type": "vehicle.control", "command": "ambient_light.set",
                         "params": {"color": "pink"}})
    assert "%" not in d2                       # 没有亮度就别硬套亮度模板
