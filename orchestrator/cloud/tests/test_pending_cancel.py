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

def test_compound_judgment_is_anaphora_not_length():
    """⚠ **2026-08-29 换判据**：分界是「余量是不是一个指着挂起的回指短语」，
    不再是「余量够不够 6 字」。这条钉住的是判据的**形状**，改它必须同时改这条断言。

    旧断言钉的是那个数字（「剥后 5 字仍是纯取消、6 字起是复合」），而那个数字正是
    真栈 turn 77 的成因：「取消**导航**」剥后 2 字 ⇒ 被判纯取消 ⇒ 导航没被取消，
    用户还收到一句关于另一件事的「已为您取消」。
    """
    # ① 短回指短语 ⇒ 纯取消（I-046 的守护面，真栈 48 次命中里占 4 次）
    assert detect_cancel("那个提醒不用了，取消吧").compound is False
    assert detect_cancel("取消刚才解锁").compound is False
    assert detect_cancel("取消刚才那笔订单").compound is False
    # ② 没有回指的实质余量 ⇒ 复合，**不论长短**（这一条是本次修的那个 bug）
    assert detect_cancel("取消导航").compound is True            # 剥后 2 字
    assert detect_cancel("不用了，关掉空调").compound is True      # 剥后 4 字
    assert detect_cancel("不用了，帮我看看天气").compound is True
    # ③ 回指只是残片、后面还挂着一整条新请求 ⇒ 仍是复合（长度在这一支里才管用）
    assert detect_cancel("算了那个不要了，先去帮我看看附近有什么景点").compound is True
    # ④ 剥完只剩承接词 ⇒ 纯取消（「先不用了吧」只剩一个「先」）
    assert detect_cancel("先不用了吧").compound is False


# ─── 评审二轮 R2（2026-09-22）：极性、问句、目标 ───

def test_negated_cancel_is_keep_not_cancel():
    """「不要取消」剥掉「取消」再剥掉「不要」余量为空 ⇒ 旧判据判成纯取消——极性反了。"""
    for t in ("不要取消", "别取消", "不用取消", "先别取消", "不取消", "不取消了", "甭取消", "不要取消。"):
        d = detect_cancel(t)
        assert d.cancelled is False, t
        assert d.act == "keep", t
        assert d.remainder == "", t


def test_negated_cancel_keeps_its_remainder():
    d = detect_cancel("不要取消，改成明晚")
    assert d.cancelled is False and d.act == "keep"
    assert "改成明晚" in d.remainder


def test_how_about_cancelling_is_still_a_cancel():
    """「要不取消吧」的「不」属于「要不」，不是否定「取消」。"""
    d = detect_cancel("要不取消吧")
    assert d.cancelled is True and d.act == "cancel" and d.compound is False


def test_asking_about_cancel_is_ask_not_cancel():
    for t in ("怎么取消", "如何取消", "咋取消", "取消了吗", "能取消吗", "可以取消吗", "能不能取消",
              "取消吗", "取消了没有", "请问怎么取消"):
        d = detect_cancel(t)
        assert d.cancelled is False, t
        assert d.act == "ask", t


def test_polite_tail_after_the_cancel_word_is_a_cancel_not_a_question():
    for t in ("取消好吗", "取消可以吗", "取消行吗", "取消吧？", "取消刚才那个可以吗", "取消，好吗"):
        d = detect_cancel(t)
        assert d.cancelled is True and d.act == "cancel", t
        assert d.compound is False, t


def test_cancel_decisions_carry_the_named_target():
    """余量剥掉回指虚词后剩下的实质名字——多条挂起并存时按它绑定目标。"""
    assert detect_cancel("取消刚才咖啡订单").target == "咖啡订单"
    assert detect_cancel("取消刚才解锁").target == "解锁"
    assert detect_cancel("取消刚才那个").target == ""
    assert detect_cancel("取消").target == ""
    assert detect_cancel("取消刚才那笔订单").target == "订单"
    assert detect_cancel("取消导航").compound is True          # 复合句不带目标（余句是新请求）


def test_negated_cancel_is_not_a_cancel_instruction_object():
    """规划侧的取消闸读同一份极性：「不要取消导航」不是「取消导航」。"""
    from orchestrator.cloud.pending_cancel import cancel_instruction_object
    assert cancel_instruction_object("不要取消导航") == ""
    assert cancel_instruction_object("别取消提醒") == ""
    assert cancel_instruction_object("取消导航") == "导航"
