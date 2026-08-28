"""`reminder.create` 侧任务性准入（C10-C）：正 2 / 硬负 2 + 误伤面。

两条判据都只覆盖**观测到的那两类**，宁可漏不误杀：漏了顶多多一条垃圾提醒，
误杀的是用户真的要做的事。所以「不许误伤」那一半的用例比「挡住」那一半多。
"""
from __future__ import annotations

import pytest

from agents.reminder.src.task_admission import admit_task_title


# ── 挡住：真栈实录的两类垃圾 title ────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "刚才那个提醒现在几点",        # 真栈实录：它被建成提醒，还被「取消第一条」选中
    "现在几点了",
    "我的提醒有哪些",
    "这条是什么",
    "明天下雨吗",
])
def test_question_shaped_titles_are_refused(title):
    ok, why = admit_task_title(title)
    assert ok is False, title
    assert why


@pytest.mark.parametrize("title", [
    "用户计划2026年国庆前往青岛4天行程",   # 真栈实录：那是一条记忆，不是待办
    "用户已确认接送安排",
    "该用户表示不吃辣",
    "客户计划下周来访",
])
def test_third_person_statements_are_refused(title):
    ok, why = admit_task_title(title)
    assert ok is False, title
    assert why


# ── 不许误伤：真任务 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "参加评审会",
    "交周报",
    "带充电线",
    "给客户回电话",
    "买牛奶",
    "接孩子",
    "吃降压药",
    "问一下张总几点开会",      # 疑问词在**句中**：这是一件真事
    "确认明天的会议室",
    "看看有什么新消息",        # 「有什么」在句中，宾语在后
])
def test_real_tasks_are_admitted(title):
    ok, why = admit_task_title(title)
    assert ok is True, f"{title} 被拒了：{why}"


def test_position_is_the_semantics():
    """**位置就是语义**——同一个疑问词在句尾是问句，在句中是定语。

    这是本判据不做「句中含疑问词就拒」的全部理由（同 N9「词表分支序就是语义」）。
    """
    assert admit_task_title("问一下张总几点开会")[0] is True
    assert admit_task_title("张总的会几点")[0] is False


def test_empty_title_is_not_this_gate_s_business():
    """空标题走调用方的 NEED_SLOT 追问那条路，不在这里判。"""
    assert admit_task_title("")[0] is True
    assert admit_task_title(None)[0] is True


def test_first_person_is_never_the_third_person_shape():
    """用户跟助手说话时说的是「我」——第三人称主语才是「别的系统在描述他」。"""
    for title in ("我要去接孩子", "我的车该保养了", "用心准备下周汇报"):
        assert admit_task_title(title)[0] is True, title
