"""取消判定——**一份词表、两条语境规则**（QA 卡 Q1-A）。

在这之前，`wait_confirm` 与 `wait_slot` 各判各的：

- `wait_confirm` 走 `_confirm_reply` 的「词占据整句」规则（`len(t) <= len(k)+3`，
  防「第二天不要去长城」含「不要」被误判成取消）；
- `wait_slot` 走子串词表 + 复合余量续处理（§37 那批的产物）。

于是**「取消刚才解锁」6 字 > 2+3，在 `wait_confirm` 下判不出取消**，挂起一直活着
——QA I-046 的原文现象（第三次单独说「取消」才清除）。判据：**同一件事的两条分支，
修了一条没修另一条**，是「同一件事有两份实现迟早有一份是错的」在**分支**上的形态
（同 B5 `stream_state.py`「判定抄两份正是 B1 那个 bug 的成因」）。

**收敛不是把一条抄给另一条，是把两条的并集写成一份。** 直接让 `wait_confirm`
复用 `wait_slot` 那套会**换一个洞**：`不订/不付/先不/不了` 只在 `wait_confirm`
的词表里，`不要了/别提醒了` 只在 `wait_slot` 的词表里。所以这里按**歧义度**分两层，
两层同属一份词表：

- **STRONG（无歧义取消短语）**：句中出现即取消。「取消刚才解锁」由它接住。
- **WEAK（裸否定词）**：只在**占据整句**时才算取消。它们做子串会误伤
  （「第二天不要去长城」含「不要」、「我吃不了这么多」含「不了」），
  而这正是 `wait_confirm` 那条整句规则本来要防的东西——**规则保留，作用域收窄到该防的那半**。

两条语境规则同源于上面这一份词表：

- `detect_cancel`：**有挂起**时用。STRONG 子串 + WEAK 整句 + 复合余量续处理。
- `is_standalone_cancel`：**没有挂起**时用（`_is_bare_confirm_word`）。只认整句。
  ⚠ 这条**必须**保持严格：放宽了「取消当前导航」会被答成「当前没有待确认的操作」，
  而不是去规划——那是 QA Q4 位置闸同款的「客户端/前置闸替编排做意图判定」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── 唯一词表 ──────────────────────────────────────────────────────────
# STRONG：语义自足的取消短语，做子串安全（旅程 B5-1 的 `_SLOT_CANCEL_RE`）。
_STRONG_WORDS = ("取消", "不用了", "算了", "不需要了", "不要了",
                 "别设了", "别提醒了", "不设了")
# WEAK：裸否定词，只在占据整句时算取消（原 `_NO_WORDS` 里 STRONG 未覆盖的那些）。
_WEAK_WORDS = ("不用", "不要", "不订", "不付", "不了", "别订", "先不")
# 「占据整句」的松弛量：与 `_confirm_reply` 的否定侧逐字一致（词长 + 3）。
_WHOLE_SENTENCE_SLACK = 3

_STRONG_RE = re.compile("|".join(_STRONG_WORDS))

# 复合取消句的「实质余量」量具（EVA 三§3，2026-08-15 真栈实测）：「算了咖啡不买了，
# 先去加点油，但还是别迟到」命中取消词后整句被吞、4.6ms 直回「已取消」——后半句是
# 新请求。剥掉取消词/标点/语气尾后余量 ≥ _COMPOUND_MIN 字 = 复合句。
#: 余量量具：把**取消词本身**从句子里剥掉，剩下的才是「用户还说了什么」。
_STRIP_RE = re.compile(
    "|".join(_STRONG_WORDS) + r"|不买了|不去了"
    r"|[，。,、！!？?；;\s]|吧$|啦$")
#: 第二遍：WEAK 词。⚠ **必须在 STRONG 之后单独跑一遍，不能并进上面那条**
#: （2026-08-29，既有用例连报两次才定形）：正则按**最左位置**匹配、不按词表顺序，
#: 「先不用了吧」会先在位置 0 命中 WEAK 的「先不」，把 STRONG 的「不用了」劈成两半，
#: 剥完剩一个「用」⇒ 被判成复合句。**「把词表按语义排好序」在同一条 alternation 里
#: 是不生效的**——语义优先级要靠分遍来表达。
#: 剥 WEAK 本身也是 2026-08-29 补的：此前只剥 STRONG，「先不」剥完还剩「不」，
#: 旧的 6 字阈值恰好把它盖住，换成新判据后当场露出来。
_WEAK_STRIP_RE = re.compile("|".join(_WEAK_WORDS) + r"|[，。,、！!？?；;\s]|吧$|啦$")


@dataclass(frozen=True)
class CancelDecision:
    """`cancelled=False` 时另外两个字段无意义（保持默认）。"""
    cancelled: bool = False
    remainder: str = ""
    compound: bool = False


_NOT_CANCEL = CancelDecision()


def _whole_sentence_hit(text: str, words: tuple[str, ...]) -> bool:
    """词近似占据整句（`len(t) <= len(k) + slack`），不做宽松子串包含。"""
    return any(k in text and len(text) <= len(k) + _WHOLE_SENTENCE_SLACK
               for k in words)


#: 分句分隔符。「不用了**，**关掉」里逗号后面还有一整个分句 ⇒ 这不是一句裸取消。
_CLAUSE_SEP_RE = re.compile(r"[，,。；;！!？?]")

#: 剥掉取消词之后允许剩下的「非实质内容」：语气词、标点、客套。
#: 判据形态与 `navigation._person_destination` 的「去掉人称词后还剩不剩实质内容」同源
#: ——**那才是「占据整句」真正想说的意思**，长度阈值只是它的一个粗糙近似。
_CANCEL_FILLER_RE = re.compile(
    r"一下|好吗|好了|谢谢|麻烦|帮我|请|[了吧啦呢的呀啊嘛哦噢喔嗯~\s"
    r"，,。；;！!？?、\.]")


#: **回指标记**：余量在指「刚才那件事」⇒ 它说的就是挂起本身，不是一个新请求。
#: 这条是「取消 X」两种含义的**唯一分界**，也是 I-046 那条修复的守护面——
#: 「取消**刚才**解锁」必须继续被判成纯取消（真栈 48 次 `system.pending_cancel`
#: 里它占 4 次）。全是指示/时间回指虚词，零领域词。
_ANAPHORA_RE = re.compile(
    r"刚才|刚刚|方才|上一(?:步|条|个|次)|前面|上面|这个|那个|这条|那条"
    r"|这次|那次|这单|那单|这件|那件|这事|那事")

#: 话语副词：它们**只是承接**，不构成一个请求。`_CANCEL_FILLER_RE` 是取消词自己的
#: 虚词面（语气/客套），这几个是**复合判定专用**的那一小片——「先不用了吧」剥完
#: 只剩一个「先」，那不是新请求。刻意不并进上面那份：`_CANCEL_FILLER_RE` 同时被
#: `is_standalone_cancel` 消费，往那里加词会顺手放宽**另一条**路径的判据。
_COMPOUND_ADVERB_RE = re.compile(r"先|就|还是|再|然后|另外|不然|干脆|反正")
#: ⚠ 这里**不许放裸「那」/「这」**：它们会把「那个」剥成「个」，
#: 回指判据当场瞎掉（既有用例「那个提醒不用了，取消吧」当场报红）。

#: 回指短语的长度上限（旧 `_COMPOUND_MIN` 的值，作用域收窄到只剩这一支）。
#: 「刚才解锁」「刚才那笔订单」是**指着挂起说的一个名词短语**；而
#: 「那个先去帮我看看附近有什么景点」里的「那个」只是被取消掉那半截的残片，
#: 后面跟着一整条新请求。**长度在这里才是它真正管用的地方**——
#: 它区分的不再是「有没有实质」，而是「这个回指短语后面还挂没挂一条新指令」。
_ANAPHORIC_PHRASE_MAX = 6


def _is_compound_remainder(remainder: str) -> bool:
    """取消词之外的余量，是**一个新请求**还是**在指挂起本身**？

    ⚠ **2026-08-29 从「余量 ≥ 6 字」换成本判据**（QA 长会话 `e15ac1e` family
    turn 77）。旧阈值把「取消**导航**」的余量数成 2 字 ⇒ 判成纯取消 ⇒
    挂起被清掉、回一句「好的，已为您取消。」，而**导航从头到尾没有被取消过**
    ——用户的指令被静默吞掉，还收到一句关于**另一件事**的完成声明（C11 那一族）。
    同族第二例，同一条阈值造成：「不用了，**关掉空调**」余量 4 字，空调也没关。

    换判据前先量了误伤面（全部长会话 artifact，48 次命中）：
    **43 次裸「取消」**（余量为空，两套判据都判纯取消，不受影响）、
    **4 次「取消刚才解锁」**（I-046 的原始用例）、**1 次就是上面那条 bug**。
    ⇒ 分界不在长度上，在**余量是不是一个指着挂起的回指短语**。

    ⚠ **既有测试当场抓到我第一版判据的两处误判**，两条都保留成了本函数的形状：
    ① 只看「有没有回指」会把「算了那个不要了，先去帮我看看附近有什么景点」判成
    纯取消——那里的「那个」是被取消掉那半截的**残片**，不是余量的主体；
    ⇒ 回指只在**短余量**里才作数。② 「先不用了吧」剥完只剩一个「先」，
    它不是新请求 ⇒ 话语副词单列一份虚词面。**反向验证要验到判据本身的形状，
    不只是验到它想修的那一条。**

    ⚠ 代价明写：挂起本身就是 X、用户又不带回指地说「取消X」时（例如确认下单期间
    说「取消订单」），新判据会**先清掉挂起、再把这句话按新请求规划**，用户可能
    多听到一句「没找到要取消的订单」。两侧代价不对称：那一侧是多一句诚实的话，
    这一侧是一条指令被吞掉 + 一句假的完成声明。
    """
    rest = _CANCEL_FILLER_RE.sub("", remainder or "")
    rest = _COMPOUND_ADVERB_RE.sub("", rest)
    if not rest:
        return False                      # 全是虚词/承接词 ⇒ 这就是一句纯取消
    if _ANAPHORA_RE.search(rest) and len(rest) <= _ANAPHORIC_PHRASE_MAX:
        return False                      # 短回指短语 ⇒ 它指的就是挂起本身
    return True


def _no_substance_left(text: str) -> bool:
    """剥掉取消词与虚词之后**一个实质字都不剩** ⇒ 这句话就是一句裸取消。

    ⚠ **2026-08-19 从「词长 + 松弛量 3」换成本判据**（QA 卡 Q8 / I-049）。旧判据是
    `len(t) <= len(k) + 3`，于是「取消」（2 字）后面跟任何**两字宾语**都算裸取消：
    真栈实测「取消静音」被答成「当前没有待确认的操作」，同族还有
    **取消导航 / 取消订单 / 取消提醒 / 取消播放 / 不用导航 / 不要开窗**——
    一整类「取消某个具体东西」的指令被前置闸吞掉，从来到不了规划。
    模块 docstring 里那句「放宽了『取消当前导航』会被答成…」写的是对的，
    只是**当时那个阈值已经放得够宽，能吞下两字宾语了**。

    ⚠ 只换这一条路径（**没有挂起**时）。`detect_cancel`（有挂起时）维持原样：
    那个语境下「取消 X」到底指挂起还是指 X 本身是真歧义，收窄它要另有证据
    ——短路型判据看到的是全部流量，误伤代价是整轮被吞（同 §9.27 取窄的理由）。
    """
    for word in _STRONG_WORDS + _WEAK_WORDS:
        if word in text and not _CANCEL_FILLER_RE.sub("", text.replace(word, "")):
            return True
    return False


def is_standalone_cancel(text: str) -> bool:
    """**没有挂起**的语境：这句话是不是一句裸取消词？只认整句。

    ⚠ 光靠「词长 + 松弛量」不够：`不用了` 是 3 字、松弛 3，于是**6 字的
    「不用了，关掉」也算整句**——真栈实测它被答成「当前没有待确认的操作」，
    而用户在下一条新指令（QA EL1，2026-08-16 抓到，是 Q1-A 引入的回归）。
    所以先看**有没有第二个分句**：逗号后面还有实质内容就不是裸取消。
    """
    t = str(text or "").strip().lower()
    if not t:
        return False
    parts = _CLAUSE_SEP_RE.split(t, 1)
    if len(parts) > 1 and parts[1].strip():
        return False                 # 还有下一个分句 ⇒ 用户在说别的事
    return _no_substance_left(parts[0])


def detect_cancel(text: str) -> CancelDecision:
    """**有挂起**的语境：这句话是不是在取消挂起？复合句的余量是什么？

    - `cancelled=False`：不是取消，按各分支原有语义继续（确认/补槽/换话题）。
    - `cancelled=True, compound=False`：纯取消——清挂起、回「已为您取消」。
    - `cancelled=True, compound=True`：取消只作用于挂起，**余句按全新请求继续处理**。
    """
    t = str(text or "").strip()
    if not t:
        return _NOT_CANCEL
    lowered = t.lower()
    if not (_STRONG_RE.search(lowered)
            or _whole_sentence_hit(lowered, _WEAK_WORDS)):
        return _NOT_CANCEL
    remainder = _WEAK_STRIP_RE.sub("", _STRIP_RE.sub("", t))
    return CancelDecision(
        cancelled=True,
        remainder=remainder,
        compound=_is_compound_remainder(remainder),
    )


# ── 没有挂起时的第三个问法：这句话是不是「取消 X」？（QA 余项，2026-08-29）──
#
# 前两个问法各有语境（`detect_cancel` 有挂起 / `is_standalone_cancel` 裸取消词），
# 这一个问的是**规划出来之后**：用户说的是一句取消，计划却在新建或在编造。
# 三个问法共用上面那一份词表——这正是本模块存在的理由，别在编排层写第四份。


#: 宾语两端的语法虚词：处置式标记与结果补语。剥它们只为让**话术里念出来的那截**
#: 通顺（「把带伞的提醒取消掉」→「带伞提醒」），判据本身不依赖它。
_OBJECT_TRIM_RE = re.compile(r"^(?:把|将|给)+|(?:掉)+$")


def cancel_instruction_object(text: str) -> str:
    """「取消 X」形态里的那个 X；不是这个形态则返回空串。

    判据三条，缺一不可：
    - **STRONG 取消词**在句中（WEAK 裸否定词做子串会误伤，见模块 docstring）；
    - **没有第二个分句**——「算了，帮我找家咖啡店」「不用了，帮我记一下明天买牛奶」
      里的取消词是**话语承接**，后面那半才是请求。这一条是本判据的主要误伤面，
      刻意用与 `is_standalone_cancel` 同一条 `_CLAUSE_SEP_RE`；
    - **剥完还剩实质内容**——裸取消（「取消」「算了」）归 `is_standalone_cancel`，
      不该在这里被判第二遍。

    真栈来历（deployed `e15ac1e`，F 留出臂 6 轮）：「取消交周报」落域散成五处，
    其中 **`reminder.create` 反向建了一条提醒**（「记下了：交周报。」）、
    **`chitchat.talk` 零动作却说「好嘞，周报提醒已经取消啦」**。
    根因是 planner 看不见用户有哪些提醒 ⇒ 这句话对它本来就是歧义的；
    **修法只要求「错得诚实」，不要求「猜得准」**（§4.2 三条候选路里代价最小的那条）。
    """
    t = str(text or "").strip()
    if not t:
        return ""
    if not _STRONG_RE.search(t.lower()):
        return ""
    parts = _CLAUSE_SEP_RE.split(t, 1)
    if len(parts) > 1 and parts[1].strip():
        return ""
    rest = _WEAK_STRIP_RE.sub("", _STRIP_RE.sub("", parts[0]))
    rest = _CANCEL_FILLER_RE.sub("", rest)
    return _OBJECT_TRIM_RE.sub("", rest).strip()
