"""评审 §4「最容易混淆的表达，应做最小对比对」——**确定性判据能分开的那几对**（P2 退出条件，2026-09-20）。

每一对写成同一个 test 里的两侧断言（正反同看，缺一边就是「收窄面只写一边」）。模型才能分的
那几对（周五去广州 vs 改成周五去 / 现在查明天是否下雨再建提醒 vs 之后一旦下雨就通知我 /
创建午休模式 vs 开启午休模式）走真栈探针 `contrast` 组，不在这里假装能离线判。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.cloud import candidate_query, pending_cancel
from orchestrator.cloud.context import references_a_candidate
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.models import SessionState
from runtime.memory_directive import is_memory_directive
from runtime.polarity import is_negated_directive
from runtime.question_shape import is_non_directive_question
from runtime.session_constraints import constraints_in, is_pure_constraint_statement


def test_pair_execute_vs_guide():
    """打开空调（执行） vs 空调怎么打开（指导）。"""
    assert is_non_directive_question("打开空调") is False
    assert is_non_directive_question("空调怎么打开") is True


def test_pair_polite_request_vs_effect_question():
    """帮我把窗关上好吗（礼貌请求） vs 关窗会影响通风吗（效果询问）——不能只看问号。"""
    assert is_non_directive_question("帮我把窗关上好吗") is False
    assert is_non_directive_question("把窗关上好吗") is False
    assert is_non_directive_question("关窗会影响通风吗") is True


def _candidates():
    return {"source_intent": "nearby.search", "purpose": "list", "items": [
        {"name": "一号店", "distance_km": 1.2, "rating": 4.5},
        {"name": "二号店", "distance_km": 3.4, "rating": 4.1},
    ]}


def test_pair_write_action_vs_read_fact_on_a_candidate():
    """导航到第二家（写动作 → 规划） vs 第二家离这里多远（读事实 → 候选集确定性直答）。"""
    assert references_a_candidate("第二家离这里多远")
    answer = candidate_query.answer("第二家离这里多远", _candidates(), [])
    assert answer and "二号店" in answer and "3.4" in answer
    assert candidate_query.answer("导航到第二家", _candidates(), []) is None, "写动作不由候选算子接管"


def test_pair_cancel_change_vs_cancel_status_question():
    """取消订单（变更） vs 订单取消了吗（状态查询）。"""
    assert pending_cancel.cancel_instruction_object("取消订单") == "订单"
    assert is_non_directive_question("取消订单") is False
    assert is_non_directive_question("订单取消了吗") is True
    assert not (pending_cancel.cancel_instruction_object("订单取消了吗")
                and not is_non_directive_question("订单取消了吗")), "问句不进取消闸"


def test_pair_stable_preference_vs_this_time_constraint():
    """记住我喜欢清淡（稳定偏好，记忆指令） vs 今天吃清淡一点（本次约束）。"""
    assert is_memory_directive("记住我喜欢清淡") is True
    assert is_pure_constraint_statement("记住我喜欢清淡") is False
    assert is_memory_directive("今天吃清淡一点") is False
    assert is_pure_constraint_statement("今天吃清淡一点") is True
    assert constraints_in("今天吃清淡一点") == {"no_spicy": True}


def test_pair_partial_replace_vs_forbid_change():
    """这家不要了，换第二家（局部取消 / 替换） vs 不要换第二家（禁止变更）。"""
    assert is_negated_directive("不要换第二家") is True
    assert is_negated_directive("这家不要了，换第二家") is False


def test_pair_quoting_confirm_vs_authorising():
    """你说"确认"是什么意思（引用 / 解释） vs 确认提交这笔订单（授权）。"""
    # 评审三轮 R3-01 B：点名授权的裁决面是挂起那一刻的已校验步骤摘要（`action_summary`），真实挂起都带它
    pending = SessionState(phase="wait_confirm", operation_id="op-1",
                           action_summary="提交这笔订单",
                           pending_plan={"goal": "提交这笔订单", "raw_text": "提交这笔订单"})
    quoted = PlannerEngine._resolve_spoken_confirm('你说"确认"是什么意思', False, [pending])
    assert quoted.kind in ("", "named_miss"), "引用「确认」不是授权"
    assert PlannerEngine._confirm_reply('你说"确认"是什么意思', False) is None
    named = PlannerEngine._resolve_spoken_confirm("确认提交这笔订单", False, [pending])
    assert named.kind == "one" and named.target is pending
