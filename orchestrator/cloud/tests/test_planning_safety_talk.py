"""安全信号在场时不许以「澄清 / 没听清」收场（QA 修复批余项 ①，2026-08-29）。

## 取证先于修法

卡上的机制写的是猜想：「『别提醒我』里含『提醒』⇒ 落域到提醒域」，并明写
**开工前先取证**。2026-08-29 在 deployed `ed53f8f` 上按 SF4 语料跑 `--repeat 5`
（每趟一条干净会话，逐轮回读 collector 的 `intents`），读数把那条猜想推翻了：

| 轮 | 落域分布（n=5）|
|---|---|
| T1「困到睁不开眼了，还要开两个小时」| `safety.driver_state` 3 / **`system.clarify` 2** |
| T2「别提醒我，继续开就行」| `chitchat.talk` 4 / `safety.driver_state` 1 |

**reminder 域一次都没出现。** 真实的失败形态比卡上写的更靠前：T1 有 2/5 落
`system.clarify`，用户听到的是「你听起来很困，接下来想怎么处理？」——而
`runtime.safety_signal.driver_state()` 这个零 LLM 判据对这句话一次都没认错。
**系统持有这个事实，却把它交给澄清卡去问用户。**

那两趟里 T2 随后由 chitchat 作答，其中一趟答的是「好的，我就不打扰你了，路上小心。」
——正是 I-043 的原始症状。chitchat 的 system prompt 里**早就写着**「不得表示可以继续
危险驾驶」，但那条 prompt 由 `meta.focus_safety_alert` 门控，而那一轮会话里
**一个疲劳信号都没登记**（见 `test_safety_focus.py` 的驾驶员状态一组）。

同族第二例在长会话侧：`INF-MANUAL-SAFETY T23`「红色机油灯亮了还能继续开吗」
同样落 `system.clarify`（C1 拦住了危险执行，但没答对）。两例同一条修法。

## 判据面刻意窄

**只在 planner 已经弃权（零步）时才接管。** 有步的轮一个字不改——「太困了，把空调
调低一点」照常执行车控（第 3 组对照）。此时的替代品只有两个：一张澄清卡，或
engine 那句「抱歉，我没听清您想让我做什么」。对一句已经说出疲劳/告警的话，
这两者都比一段分级建议差。

产物复用既有机制（`_talk_only_plan` 的第四个调用方），**不新增路由、不加正则**：
兜底 Agent 自带 `runtime.safety_signal` 的分级建议，并经保留键 `_safety_alert`
把会话态登记上——「答一句」与「记下来」在这条修法里是同一个动作。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder, _assemble_capability_catalog

from tests.test_planning import MockAgent

#: 模型出澄清卡而不产步——真栈那 2/5 就是这个形态。
_CLARIFY = ('{"addressed":true,"steps":[],"clarify":{"question":"你想让我怎么处理？",'
            '"options":[{"label":"找服务区","send_text":"找最近的服务区"},'
            '{"label":"放点音乐","send_text":"放点音乐"}]}}')
#: 第二种形态：模型什么都不给。
_EMPTY = '{"addressed":true,"steps":[]}'


@pytest.fixture(autouse=True)
def _offline_retrieval(monkeypatch):
    """范例检索默认 hybrid 会打 llm-gateway Embed（网络）。单测必须离线确定。"""
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _agents():
    return [MockAgent("chitchat", ["chitchat.talk"],
                      response_only=("chitchat.talk",)),
            MockAgent("edge-vehicle", ["hvac.set"],
                      kind="edge_fast", deployment="edge"),
            MockAgent("navigation", ["navigation.navigate_to"])]


def _build(text: str, reply: str = _CLARIFY, agents=None):
    agents = agents if agents is not None else _agents()

    async def mock_llm(messages):
        return reply

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    return asyncio.run(builder.build(text, WorkingSet(catalog=agents),
                                     PlanContext(session_id="t")))


# ── 1. 正向：安全信号 + 零步 ⇒ 兜底 Agent 答一句 ─────────────────────────────

@pytest.mark.parametrize("text", [
    "困到睁不开眼了，还要开两个小时",     # 真栈 SF4 T1，2/5 落 system.clarify
    "我太困了",                            # 驾驶员状态：疲劳
    "刚喝了酒，还能开吗",                  # 驾驶员状态：酒后（不可让步的那一档）
    "红色机油灯亮了还能继续开吗",          # 车辆告警：长会话 T23 同形态
    "胎压黄灯亮了",                        # 车辆告警：amber 也算
])
def test_safety_signal_with_empty_plan_falls_back_to_talk(text):
    plan = _build(text)
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], (
        f"「{text}」仍是空计划 —— 用户会听到一句澄清或「没听清」")
    assert plan.clarify is None, "接管之后澄清卡必须清掉，否则 engine 仍会短路出卡"
    assert (plan.plan_mode or "").endswith("_safety_talk"), plan.plan_mode


def test_bare_empty_plan_with_a_safety_signal_also_answers():
    """第二种形态：模型什么都不给。用户可见结果必须相同。

    ⚠ **这一条不是被本次改动决定的**（反向验证实测：注掉安全闸它照样绿）——
    纯空 steps 早被 `_no_action` 那条接住了，本闸接的是**带澄清卡**的那一路。
    留着它是因为「两种形态的用户可见结果相同」本身是要守的契约；
    但读的人别把它算成本闸的证据（§4.3「要验到哪一条断言真的被这次改动决定」）。
    """
    plan = _build("困到睁不开眼了，还要开两个小时", reply=_EMPTY)
    assert [s.intent for s in plan.steps] == ["chitchat.talk"]


# ── 2. 误伤对照：没有安全信号时，澄清能力一个字不动 ──────────────────────────

@pytest.mark.parametrize("text", [
    "云岚国际中心",                # 裸对象，路由歧义 ⇒ 就该问
    "没开定位为什么还有距离",
    "有点累",                      # **模糊说法不进词表**（safety_signal 纪律：宁可漏接）
    "前面路口右转",
])
def test_ordinary_empty_plan_still_clarifies(text):
    """把澄清一并改写成闲聊，是方向相反、同样严重的错。"""
    plan = _build(text)
    assert not plan.steps and plan.clarify is not None, (
        f"「{text}」的澄清卡被本闸吃掉了")
    assert not (plan.plan_mode or "").endswith("_safety_talk")


# ── 3. 误伤对照：安全词 + 有步 ⇒ 逐字不变（安全对话不剥夺用户开空调的权利）────

def test_safety_words_do_not_hijack_a_planned_step():
    """**判据只在 planner 弃权时生效**——它不看会话里有没有告警，也不抢已有的步。

    与 `test_safety_focus.py` 里那句「刻意不做」一脉相承：不加「安全语境下禁止一切
    无关车控」的硬闸。
    """
    agents = _agents()
    catalog = _assemble_capability_catalog(agents)
    ref = catalog.pair_to_ref[("edge-vehicle", "hvac.set")]
    wire = ('{"steps":[{"id":"s1","capability_ref":"%s",'
            '"slots":{"temp":"22"},"depends_on":[],"slot_refs":{}}]}' % ref)
    plan = _build("太困了，把空调调低一点", reply=wire, agents=agents)
    assert [s.intent for s in plan.steps] == ["hvac.set"], (
        "有步的轮被安全闸接管了——那是本闸明确不做的事")
    assert not (plan.plan_mode or "").endswith("_safety_talk")


# ── 4. 找不到兜底 Agent 时不许把计划改坏 ────────────────────────────────────

def test_without_a_fallback_agent_the_plan_is_left_alone():
    """`_talk_only_plan` 返回 None 时保持原样（澄清卡仍在），不许留下半份改动。"""
    agents = [MockAgent("navigation", ["navigation.navigate_to"])]
    plan = _build("困到睁不开眼了，还要开两个小时", agents=agents)
    assert not plan.steps and plan.clarify is not None
    assert not (plan.plan_mode or "").endswith("_safety_talk")


# ── 5. 本闸的代价，**钉成可见断言**：交通信号灯的「黄灯」也会被当成车辆告警 ────────

@pytest.mark.parametrize("text", ["前面黄灯了", "刚才那个红灯我是不是闯了"])
def test_traffic_light_words_also_route_to_the_fallback_agent(text):
    """这一类**会被接管**——它是本闸的代价，不是遗漏，所以钉在这里而不是藏着。

    ## 为什么代价可以接受

    `runtime.safety_signal.WARNING_LIGHTS` 里有「黄灯」「红灯」（它们要认的是仪表盘
    告警灯），交通信号灯的说法会一并命中。但**本闸不产生这个误判、只是让它可达**：
    chitchat 的 `_safety_answer` 对任何路由到它的轮都是这么判的，变的只是
    「零步轮以前落到『抱歉，我没听清』，现在落到兜底 Agent」。
    **两个都答不对，而后者至少答了一句**——且它零动作、不碰车控。

    真要收窄，该收的是 `WARNING_LIGHTS` 那张表（把「黄灯/红灯」换成要求仪表盘语境），
    **不是在这里加一条「交通灯例外」**——那会让本闸开始认识领域词。
    """
    plan = _build(text)
    assert [s.intent for s in plan.steps] == ["chitchat.talk"]
    assert (plan.plan_mode or "").endswith("_safety_talk")
