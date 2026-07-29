"""端云透传与分歧观测（M5 P2-D2）。

钉三条性质：
  ① 端侧初判**不进 prompt**——Shadow NLU 实测端侧规则臂 domain 准确率 75.9%、LLM 91.2%，
     把更差的判断塞进更好的模型的上下文是负期望的赌，要开须先有 A/B 数据；
  ② 分歧比的是**域**不是 intent——端侧 `hvac.on` 与云侧 `hvac.set` 是同一判断的粗细之分，
     记成分歧只会把噪声灌进标注队列；
  ③ 端侧没判时**不发字段**（少一个恒空的观测列）。
"""
from __future__ import annotations

import inspect

from orchestrator.cloud.engine import _edge_nlu_attrs
from orchestrator.cloud.models import PlanContext, Plan, Step


def _plan(*intents):
    return Plan(steps=[Step(id=f"s{i}", agent_id="a", intent=x)
                       for i, x in enumerate(intents)])


def test_same_domain_coarse_vs_fine_is_not_divergence():
    ctx = PlanContext(edge_nlu="hvac.on|0.92")
    assert _edge_nlu_attrs(ctx, _plan("hvac.set"))["edge_agree"] == "1"


def test_cross_domain_is_divergence():
    ctx = PlanContext(edge_nlu="hvac.on|0.92")
    a = _edge_nlu_attrs(ctx, _plan("chitchat.talk"))
    assert a["edge_agree"] == "0" and a["edge_nlu"] == "hvac.on|0.92"


def test_empty_plan_counts_as_divergence():
    """端侧有判断、云侧出空计划——正是最该被人看一眼的一类。"""
    assert _edge_nlu_attrs(PlanContext(edge_nlu="hvac.on|0.9"), _plan())["edge_agree"] == "0"


def test_no_edge_judgement_emits_nothing():
    assert _edge_nlu_attrs(PlanContext(), _plan("a.b")) == {}


def test_edge_nlu_never_enters_planner_prompt():
    """性质①的源码级断言：user message 拼装里不得出现 edge_nlu。
    这条是**架构判断**不是实现细节——真要开必须显式改这里并附 A/B 数据。"""
    from orchestrator.cloud.planning import PlanBuilder
    src = inspect.getsource(PlanBuilder._planner_user_msg)
    assert "edge_nlu" not in src
