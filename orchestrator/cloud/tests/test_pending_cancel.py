"""挂起态取消判定的唯一实现（QA 卡 Q1-A）。

**这一族存在的理由**：`wait_confirm` 与 `wait_slot` 曾各有一套取消判据——
前者走 `_confirm_reply` 的「词占据整句」（`len(t) <= len(k)+3`），后者走
`_SLOT_CANCEL_RE` 子串 + 复合余量续处理（§37 那批的产物）。于是
**「取消刚才解锁」（6 字 > 2+3）在 `wait_confirm` 下判不出取消**，挂起一直活着
（QA I-046 的原文现象：第三次单独说「取消」才清除）。

判据：**同一件事的两条分支，修了一条没修另一条**——「同一件事有两份实现，
迟早有一份是错的」在**分支**上的形态。收敛后两条分支共用
`pending_cancel.detect_cancel`。
"""
from __future__ import annotations

from orchestrator.cloud.pending_cancel import detect_cancel, is_standalone_cancel


# ─── 纯取消：剥词后余量 < 阈值 ───

def test_bare_cancel_words_are_pure_cancel():
    for t in ("取消", "不用了", "算了", "不要了", "别设了"):
        d = detect_cancel(t)
        assert d.cancelled is True, t
        assert d.compound is False, t


def test_cancel_with_short_reference_is_pure_cancel():
    """QA I-046 原文形态：「取消刚才解锁」——旧 `wait_confirm` 判据在这里失效。"""
    for t in ("取消刚才解锁", "取消刚才的解锁", "那个提醒不用了，取消吧",
              "算了不用了", "先不用了吧"):
        d = detect_cancel(t)
        assert d.cancelled is True, t
        assert d.compound is False, t


# ─── 复合取消：取消挂起 + 余句按新请求继续（§37 已验证的行为，不得回退）───

def test_compound_cancel_keeps_remainder():
    d = detect_cancel("算了咖啡不买了，先去加点油，但还是别迟到")
    assert d.cancelled is True
    assert d.compound is True
    assert "加点油" in d.remainder


def test_compound_cancel_of_qa_case():
    d = detect_cancel("算了那个不要了，先去帮我看看附近有什么景点")
    assert d.cancelled is True and d.compound is True


# ─── 不是取消 ───

def test_non_cancel_text():
    for t in ("确认", "好的", "晚上九点", "导航去南山科技园", "", "   "):
        assert detect_cancel(t).cancelled is False, t


def test_cancel_word_inside_unrelated_request_is_compound_not_silent():
    """「帮我取消明天的会议提醒」——含取消语义，余量长 ⇒ 复合：清挂起 + 余句继续。

    **刻意不判成「不是取消」**：挂起语境里出现取消词，用户几乎总是在指挂起的那件事；
    余量长时不吞掉后半句才是修法（§37），不是把整句判成非取消。
    """
    d = detect_cancel("帮我取消明天的会议提醒")
    assert d.cancelled is True and d.compound is True


# ─── 收敛不得换一个洞：两条旧分支的词表并集都要在 ───

def test_weak_words_from_the_wait_confirm_side_survive():
    """`不订/不付/先不/不了` 原本只在 `wait_confirm` 的 `_NO_WORDS` 里。
    直接让 wait_confirm 复用 wait_slot 那套词表 = 补一个洞挖一个洞。"""
    for t in ("不订", "不付了", "先不", "不了", "不用", "不要"):
        assert detect_cancel(t).cancelled is True, t


def test_weak_words_do_not_match_as_substring():
    """WEAK 层只在占据整句时算取消——这是 `wait_confirm` 那条整句规则真正该防的面，
    作用域收窄但**不取消**。"""
    for t in ("第二天不要去长城", "我吃不了这么多", "把不用的提醒都留着"):
        assert detect_cancel(t).cancelled is False, t


def test_strong_words_from_the_wait_slot_side_survive():
    """`不要了/别提醒了/不设了` 原本只在 `wait_slot` 的 `_SLOT_CANCEL_RE` 里。"""
    for t in ("那个不要了", "别提醒了", "这条不设了", "不需要了"):
        assert detect_cancel(t).cancelled is True, t


# ─── 无挂起语境：必须保持严格（否则「取消当前导航」被答成「没有待确认的操作」）───

def test_standalone_cancel_is_whole_sentence_only():
    assert is_standalone_cancel("取消") is True
    assert is_standalone_cancel("不用了") is True
    assert is_standalone_cancel("取消当前导航") is False
    assert is_standalone_cancel("取消刚才解锁") is False
    assert is_standalone_cancel("别开始导航") is False


def test_a_second_clause_means_it_is_not_a_bare_cancel():
    """⚠ 2026-08-16 回归修复：光靠「词长 + 松弛量」不够。

    `不用了` 是 3 字、松弛 3 ⇒ **6 字的「不用了，关掉」也算整句**，
    真栈实测它被答成「当前没有待确认的操作」，而用户在下一条新指令
    （QA EL1，这是 Q1-A 引入的回归）。逗号后面还有实质内容就不是裸取消。
    """
    assert is_standalone_cancel("不用了，关掉") is False
    assert is_standalone_cancel("算了，打开车窗") is False
    assert is_standalone_cancel("取消，帮我导航") is False
    # 对照：分隔符后没有内容仍是裸取消
    assert is_standalone_cancel("不用了。") is True
    assert is_standalone_cancel("取消！") is True


# ─── 反向：注入缺陷要红（§4.3「恒绿的断言比没有更糟」）───

def test_threshold_is_the_documented_one():
    """阈值是 6 字。剥后 5 字仍是纯取消、6 字起是复合——这条钉住的是那个具体数字，
    改阈值必须同时改这条断言，不能让它悄悄跟着实现走。"""
    assert detect_cancel("那个提醒不用了，取消吧").compound is False   # 剥后 5 字
    assert detect_cancel("不用了，帮我看看天气").compound is True      # 剥后 6 字
