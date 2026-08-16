"""槽值保真（Q12）在编排里的接线 + **执行路径覆盖面守卫**。

判据本身在 `runtime/slot_fidelity.py`（唯一实现，正反两向在 `runtime/tests` 里测）。
这一份只管两件编排层的事：

1. **接线对不对**——`_resolve_slot_refs` 出来的槽位已经带回限定词，
   且服务端权威解析出来的值不被覆盖。
2. **覆盖面**——挂在 `_resolve_slot_refs` 上的东西必须在**每条执行路径**上都生效。
   这一族本项目已经踩过三次：M2 的 Verifier 漏 D0、2026-08-13 的门店锚定漏 D0、
   本批发现 `loop.py`（T2 单步流式）**传了函数却漏了 ctx**——于是任何依赖 ctx
   的挂点在那条路上静默失效。修法是把覆盖面变成断言，不是「下次记得」。
"""
from __future__ import annotations

import re
from pathlib import Path

from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import PlanContext, Step, StepResult, StepStatus

_CLOUD = Path(__file__).resolve().parents[1]
I008 = "明天下午四点提醒我开会，三点半再提醒我一次"


def _ctx(raw: str) -> PlanContext:
    return PlanContext(session_id="s", user_id="u", raw_text=raw)


def test_dropped_time_qualifier_is_restored_before_dispatch():
    """真栈原句：planner 把第二个时刻转述成裸「三点半」时，下发前补回「明天下午」。

    ⚠ 这条**不是**真栈能翻绿的读数——同一句话 MiniMax 当天三次取样都**没有**丢
    限定词（`time_text="明天下午三点半"`）。丢不丢是模型方差，而这道闸要消灭的
    正是「答案对不对取决于这次模型怎么想」。所以证据是注入式的：
    把模型**可能**产出的那个值直接摆进 slots，看闸认不认。
    """
    step = Step(id="s2", agent_id="reminder", intent="reminder.create",
                slots={"title": "开会", "time_text": "三点半"})

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, {}, _ctx(I008))

    assert step.slots["time_text"] == "明天下午三点半"
    assert step.slots["title"] == "开会"        # 非时刻槽一个字不动


def test_a_value_the_model_kept_intact_is_left_alone():
    """对照：模型没丢限定词的那条（真栈实测形态）不许被再加工。"""
    step = Step(id="s1", agent_id="reminder", intent="reminder.create",
                slots={"title": "开会", "time_text": "明天下午四点"})

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, {}, _ctx(I008))

    assert step.slots["time_text"] == "明天下午四点"


def test_server_resolved_values_are_never_rewritten_from_raw():
    """slot_ref 解析出来的值带 provenance，原话不许覆盖它。"""
    done = {"s1": StepResult(step_id="s1", status=StepStatus.OK,
                             source_intent="info.query",
                             data={"items": [{"t": "三点半"}]})}
    step = Step(id="s2", agent_id="reminder", intent="reminder.create",
                slot_refs={"time_text": "s1.data.items.0.t"})

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, done, _ctx(I008))

    assert step.slots["time_text"] == "三点半"


def test_missing_ctx_is_a_no_op_not_a_crash():
    """ctx 缺席（旧调用点/端侧 mini-plan）时原样通过——诚实不动，不炸。"""
    step = Step(id="s1", agent_id="reminder", intent="reminder.create",
                slots={"time_text": "三点半"})
    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(step, {}, None)
    assert step.slots["time_text"] == "三点半"


def test_every_dispatch_path_passes_ctx_into_resolve_slot_refs():
    """**覆盖面断言**：`_resolve_slot_refs` 的每个调用点都必须传第三个参数 ctx。

    少传 ctx 不会报错、不会有日志、不会有任何症状——挂在它上面的东西只是
    「在这条路上不发生」。`loop.py` 就这样断了：门店锚定与城市补全在 T2 单步流式
    上从来没生效过，而 D0 那条同族缺陷 2026-08-13 修过一次、**只补了 D0**。
    """
    call = re.compile(r"_resolve_slot_refs\(\s*([^)]*)\)", re.S)
    offenders = []
    for path in sorted(_CLOUD.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        for m in call.finditer(src):
            args = m.group(1)
            if args.lstrip().startswith("self,"):
                continue                              # 定义处，不是调用
            if len([a for a in args.split(",") if a.strip()]) < 3:
                offenders.append(f"{path.name}: _resolve_slot_refs({args.strip()})")
    assert not offenders, (
        f"这些调用点没传 ctx：{offenders}。"
        "少传不会报错，只会让挂在这个函数上的东西在那条执行路径上静默不生效。")


def test_the_coverage_guard_would_catch_a_two_arg_call():
    """注入验红：守卫必须够得着现实里那个写法（`(step, done_seed)`）。"""
    call = re.compile(r"_resolve_slot_refs\(\s*([^)]*)\)", re.S)
    bad = call.search("self.executor._resolve_slot_refs(step, done_seed)")
    assert bad and len([a for a in bad.group(1).split(",") if a.strip()]) < 3
    good = call.search("self.executor._resolve_slot_refs(step, done_seed, ctx)")
    assert good and len([a for a in good.group(1).split(",") if a.strip()]) == 3
