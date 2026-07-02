"""统一复杂度判据 + 过程区四阶段文案合成（脱敏）。"""
from orchestrator.cloud.models import Plan, Step, StepResult, StepStatus
from orchestrator.cloud.progress import (
    is_complex, phase_label, capability_label, task_summary,
    plan_steps_summary, step_summary)


# is_complex 现读 Step.heavy（P3：由 manifest capability.heavy 经 _validated_steps 落地），
# 测试按此模拟：重域意图的步 heavy=True，轻查询/闲聊/车控 heavy=False。
_HEAVY = {"trip.plan", "trip.modify", "info.search", "info.news", "research.run", "charging.plan"}


def _plan(*intents, complexity="simple", slots=None):
    steps = [Step(id=f"s{i}", agent_id="a", intent=it, slots=(slots or {}),
                  heavy=(it in _HEAVY))
             for i, it in enumerate(intents)]
    return Plan(steps=steps, complexity=complexity)


def test_is_complex():
    assert is_complex(_plan("trip.plan")) is True            # 单 heavy 意图
    assert is_complex(_plan("info.search")) is True          # 深度调研
    assert is_complex(_plan("info.weather", "info.stock")) is True  # 多步
    assert is_complex(_plan("hvac.set", complexity="adaptive")) is True  # T2
    # 普通单条轻查询 / 闲聊 / 车控 → 不复杂
    assert is_complex(_plan("info.weather")) is False
    assert is_complex(_plan("chitchat.talk")) is False
    assert is_complex(_plan("hvac.set")) is False
    assert is_complex(Plan(steps=[])) is False
    assert is_complex(None) is False


def test_phase_label_is_verb_phrase():
    """执行阶段动作短语（「正在{label}…」要通顺）。"""
    assert phase_label("trip.plan") == "编排行程"
    assert phase_label("info.weather") == "查询天气"
    assert phase_label("info.search") == "联网检索"
    assert phase_label("charging.plan") == "规划充电"
    assert phase_label("navigation.navigate_to") == "规划路线"
    assert phase_label("totally.unknown") == "处理中"


def test_capability_label_is_noun():
    """规划阶段能力名（名词）。"""
    assert capability_label("trip.plan") == "行程规划"
    assert capability_label("info.weather") == "天气查询"
    assert capability_label("info.search") == "联网搜索"
    assert capability_label("charging.plan") == "充电规划"
    assert capability_label("navigation.navigate_to") == "路线规划"


def test_task_summary_natural_language():
    multi = _plan("trip.plan", "info.forecast", "charging.plan")
    assert task_summary(multi) == "识别为多步骤出行规划任务"
    single = _plan("info.search")
    assert task_summary(single) == "识别为信息调研任务"


def test_plan_steps_summary_lists_capabilities():
    plan = _plan("trip.plan", "info.forecast", "charging.plan")
    assert plan_steps_summary(plan) == "行程规划、天气查询、充电规划"
    # 去重保序：同能力只列一次
    dup = _plan("info.weather", "info.forecast")
    assert plan_steps_summary(dup) == "天气查询"


def test_step_summary_counts_natural():
    step = Step(id="s1", agent_id="nav", intent="navigation.search_poi")
    poi = StepResult(step_id="s1", status=StepStatus.OK, speech="已为您找到附近地点",
                     ui_card={"type": "poi_list", "items": [{"name": "a"}, {"name": "b"}]})
    assert step_summary(step, poi) == "已找到 2 个地点"
    charge = StepResult(step_id="s2", status=StepStatus.OK, speech="x",
                        ui_card={"type": "charging_route", "stops": [{"name": "站1"}]})
    assert step_summary(step, charge) == "已规划 1 个充电点"
    search = StepResult(step_id="s3", status=StepStatus.OK, speech="x",
                        ui_card={"type": "search_result", "sources": [{"t": 1}, {"t": 2}]})
    assert step_summary(step, search) == "已综合 2 个来源"


def test_step_summary_keeps_full_first_sentence():
    """关键信息（如股价数字）不被腰斩——取完整首句（≤60 字不截）。"""
    step = Step(id="s1", agent_id="info", intent="info.stock")
    r = StepResult(step_id="s1", status=StepStatus.OK,
                   speech="英伟达当前价 200.04，跌 8.61（-4.13%）。今日成交活跃。")
    s = step_summary(step, r)
    assert s == "英伟达当前价 200.04，跌 8.61（-4.13%）"   # 首句完整、数字不被截
    assert "…" not in s


def test_step_summary_does_not_leak_internal_fields():
    step = Step(id="s1", agent_id="info", intent="info.search",
                slots={"query": "SECRET_QUERY"},
                meta={"thinking": "on", "internal_token": "XYZ"})
    r = StepResult(step_id="s1", status=StepStatus.OK,
                   speech="综合多个来源给出结论。",
                   data={"raw_prompt": "你是严谨的车载信息编辑 SYSTEM_PROMPT",
                         "reasoning": "内部推理链"},
                   ui_card={"type": "search_result", "sources": [{"t": 1}]})
    s = step_summary(step, r)
    for leak in ("SECRET_QUERY", "SYSTEM_PROMPT", "内部推理链", "internal_token",
                 "thinking", "raw_prompt"):
        assert leak not in s
