"""Deferred conditions: does an instruction wait on something? Shared by edge and cloud.

In 「如果…就…」 or 「温度低于20度时…」 whether the second half runs depends on the
first. The cloud planner upgrades such plans to its bounded loop and never splits
the clauses. The edge must not run any part of them locally either: splitting the
sentence executes the consequent and drops the condition. The CA2-10 real-stack
probe (2026-10-02) saw 「如果深圳今天不下雪，就把空调打开」 split at the edge and
the air conditioning switched on unconditionally. This is the only copy of the rule.

The cloud asks the same question through ``waits_on_condition``: the same rule
with one exclusion, 「只要…的」 meaning "only the ones that…" (no consequent).
The edge keeps the broad ``is_deferred_instruction`` on purpose.

2026-10-19 补两族（设计 docs/design/2026-10-13-v2-200x5-followups.md §23）：端侧离线复现，下面这些说法全被当场执行——
- 没有框架词的「X 的话，Y」：「下雨的话把车窗关上」当场 `window.close`。它和「如果 X 的话」一样是条件句：端云都等条件（`waits_on_condition`），
  句首的那种是条件指令（`split_conditional_instruction`，走 T2 前件判定）。
- 要往后推的说法：「等会儿再把车窗打开」「到了公司再开空调」「十分钟后再打开空调」「明天早上八点打开空调」「下班的时候打开空调」。
  端侧一律上云；云端只拦当场的端侧车控（`defers_to_later`，规划器的问句闸消费），定时场景、提醒这类「到时候再做」的安排照常——
  规划器对这些说法多半建提醒（只规划取证：「十分钟后再打开空调」6 遍 5 遍提醒），可「等会儿再把车窗打开」6 遍 5 遍当场 `window.open`。
"""
from __future__ import annotations

import re

from runtime.cntime import CN_NUM_SRC, DAY_ALT, DAY_WORDS, SEG_ALT, cn_int
from runtime.question_shape import is_non_directive_question

DEFERRED_CONDITION_RE = re.compile(
    r"(?:如果|要是|假如|若|只要|除非|"
    r"(?:不够|不足|超过|低于|高于|达到|满足).{0,40}?(?:就|则|时|后|才)|"
    r"(?:根据|依据).{0,40}(?:结果|情况).{0,20}(?:决定|选择|判断)|"
    r"\bif\b|\bwhen\b|\bunless\b)",
    re.IGNORECASE,
)
COMPLETE_DEFERRED_CONDITION_RE = re.compile(
    r"(?:如果|要是|假如|若|只要|除非).{1,80}?(?:就|则|才|便|的话)"
    r"[^，,。；;！？!?\n]{1,80}"
    r"|\bif\b.{1,120}?\bthen\b.{1,120}"
    # 没有框架词、以「X 的话」起句（2026-10-19）；客气 / 承上文这类不是条件的，拆句时由 `split_conditional_instruction` 排除
    r"|^[^，,。；;！？!?\n]{1,40}?的话[，,]?[^，,。；;！？!?\n]{1,80}",
    re.IGNORECASE,
)


# ── 没有框架词的「X 的话」（2026-10-19）────────────────────────────────────────────────
#: 句首或分句首的「X 的话」。X 是下面几类时不是条件：客气（同 `_POLITE_CONDITION_RE`：「可以的话」）、承上文（「这样的话 / 那样的话」，
#: 条件上文已经给定）、话题（「我的话」「你的话」）、名词的「话」（「他说的话」）。
_BARE_IF_RE = re.compile(r"(?:^|(?<=[，,。；;！!？?\s]))(?P<condition>[^，,。；;！!？?\s]{1,20}?)的话")
_LEADING_BARE_IF_RE = re.compile(r"^(?P<condition>[^，,。；;！？!?\n]{1,40}?)的话[，,]?")
_IF_FILLER_RE = re.compile(r"^(?:那么?|那就|嗯|哦)")
_ANAPHORIC_IF_RE = re.compile(r"(?:这|那)(?:样子?|么|么说|样说|个样子)?|如此|这么着|那么着|(?:要)?不然|否则|要不")
_TOPIC_IF_RE = re.compile(r"(?:我|你|您|他|她|它)们?|咱们?|大家|自己")
_SAID_IF_RE = re.compile(r"(?:说|讲|问|聊|骂|夸|答|回|写|留|交代|叮嘱|嘱咐)(?:过|了)?$")
#: 「谁的话」：以人称 / 别人收尾的是「某人说的话」或话题（「转述别人的话」「老板的话你记一下」里的「别人」）。
_WHOSE_IF_RE = re.compile(r"(?:别人|人家|他人|对方|大家|我|你|您|他|她|它)们?$")


def _not_a_condition(c: str) -> bool:
    return bool(not c or _POLITE_CONDITION_RE.fullmatch(c) or _ANAPHORIC_IF_RE.fullmatch(c)
                or _TOPIC_IF_RE.fullmatch(c) or _SAID_IF_RE.search(c) or _WHOSE_IF_RE.search(c))


def _real_condition(condition: str) -> str:
    """「X 的话」里的 X 是不是真条件：是 ⇒ 去掉句首语气词后的 X；不是 ⇒ ""。剥之前也要看（「那样的话」的「那」不是语气词）。"""
    raw = str(condition or "")
    c = _IF_FILLER_RE.sub("", raw)
    return "" if _not_a_condition(raw) or _not_a_condition(c) else c


def _has_bare_condition(text: str) -> bool:
    return any(_real_condition(m.group("condition")) for m in _BARE_IF_RE.finditer(text))


# ── 往后推的说法（2026-10-19）──────────────────────────────────────────────────────────
#: 将来的日子（「明天 / 后天 / 今晚」，日词表 `runtime.cntime.DAY_WORDS` 里天偏移 ≥ 1 或内嵌晚上的那些）。
_FUTURE_DAY_ALT = "|".join(word for word, offset, segment in DAY_WORDS if offset >= 1 or segment == "eve")
#: 说不出时刻的往后推：等会儿 / 一会儿 / 待会儿 / 稍后 / 晚点 / 回头 / 改天。
_VAGUE_LATER = r"(?:等(?:一?会儿?|一下|下)|一会儿?|待会儿?|过一?会儿?|稍后|晚点|晚些|回头|改天)"
#: 往后推的时间词：上面那些，一天里的时段，将来的日子。
_LATER_WORD = rf"(?:{_VAGUE_LATER}|{SEG_ALT}|{_FUTURE_DAY_ALT})"
#: 时间词之后再做：「等会儿再把车窗打开」「晚上再开空调」「一会儿到家了再开空调」。时间词作定语的不算（「今晚的电影再放一遍」）。
_LATER_THEN_RE = re.compile(rf"{_LATER_WORD}(?![的那])[^，,。；;！!？?\s]{{0,20}}?再", re.IGNORECASE)
#: 到了某处 / 等某件事之后再做：「到了公司再开空调」「等我到家再开空调」「到家后再开空调」。
_ARRIVE_THEN_RE = re.compile(r"(?:^|(?<=[，,。；;！!？?\s])|等我?|先)到了?[^，,。；;！!？?\s]{0,12}?再")
#: 一段时长之后：「十分钟后」「过半小时」「两个小时以后」。
_AFTER_DURATION_RE = re.compile(
    rf"(?:{CN_NUM_SRC}|半)\s*个?(?:分钟|小时|钟头|秒钟?)(?:以?后|之后)|过(?:{CN_NUM_SRC}|半)\s*个?(?:分钟|小时|钟头|秒钟?)")
#: 将来的日子单说也是往后推（「明天打开空调」「我明天早上要开空调」）：只认分句开头（前面至多一个主语）——作定语的（「明天的天气」）、
#: 名字里的（「播放明天会更好」）不算。
_FUTURE_DAY_RE = re.compile(rf"(?:^|(?<=[，,。；;！!？?\s我你您咱他她]))(?:{_FUTURE_DAY_ALT})(?!的)", re.IGNORECASE)
#: 时刻：`HH:MM`，或「N 点」带上「钟 / 半 / 一刻 / 三刻 / N 分」。
_CLOCK_RE = re.compile(
    rf"(?:(?:[01]?\d|2[0-3])[:：][0-5]\d|(?P<hour>{CN_NUM_SRC})\s*点(?P<tail>\s*(?:钟|半|一刻|三刻|{CN_NUM_SRC}\s*分))?)")
_DECIMAL_AFTER_RE = re.compile(rf"\s*{CN_NUM_SRC}")
_DAYTIME_BEFORE_RE = re.compile(rf"(?:{SEG_ALT}|{DAY_ALT})\s*$", re.IGNORECASE)
#: 「X 的时候」：X 以指示 / 现在 / 疑问 / 「有时候」「小时候」这类收尾时不是往后推（「这首歌我小时候经常听」）。
_WHEN_RE = re.compile(r"(?P<when>[^，,。；;！!？?\s]{1,20}?)的?时候")
_NOT_LATER_WHEN_RE = re.compile(
    r"(?:(?:这|那|此)(?:个|种)?|现在|当下|当前|目前|平时|平常|有|小|什么|啥|任何|这会儿|那会儿)$")
#: 「X 了再 Y」（「雨停了再开窗」）只让端侧上云：云端对「空调关了再开一下」这种当下依次做的两件事本来就排对了（只规划取证 6 / 6）。
_DONE_THEN_RE = re.compile(r"了再(?!见)")


def _has_clock_time(text: str) -> bool:
    """有没有一个时刻。「二十六点五度」「评分四点五」是小数、「调高一点」是「一些」，都不算：小时要在 0–24、「点」后面不能紧跟数字
    （「八点十分」走带「分」的那一支），「一点 / 两点」光杆不算——要有时段 / 日词在前（「下午一点」）或带「钟 / 半 / N 分」；
    「第三点」是序数。"""
    for m in _CLOCK_RE.finditer(text):
        if m.group("hour") is None:
            return True
        hour = cn_int(m.group("hour"))
        if hour is None or hour > 24 or text[max(0, m.start() - 1):m.start()] == "第":
            continue
        if m.group("tail"):
            return True
        if _DECIMAL_AFTER_RE.match(text, m.end()):
            continue
        if hour <= 2 and not _DAYTIME_BEFORE_RE.search(text[:m.start()]):
            continue
        return True
    return False


def _has_later_when(text: str) -> bool:
    for m in _WHEN_RE.finditer(text):
        when = m.group("when").removesuffix("的")
        if when and not _NOT_LATER_WHEN_RE.search(when):
            return True
    return False


def defers_to_later(text) -> bool:
    """这句指令把要做的事往后推了（到某个时间、某件事之后、「X 的时候」）：当场的端侧车控不能做（规划器的问句闸消费，云端唯一一处）。

    只看形态、零领域词；不管推到的那一刻能不能判断（那是提醒 / 场景自己的事）。问句不算：「明天天气怎么样」里的「明天」
    不是把哪件事往后推，端侧本地照答（语料对照里只有这一类问句被多送上云）。"""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return bool(t) and not is_non_directive_question(t) and bool(
        _LATER_THEN_RE.search(t) or _ARRIVE_THEN_RE.search(t) or _AFTER_DURATION_RE.search(t)
        or _FUTURE_DAY_RE.search(t) or _has_clock_time(t) or _has_later_when(t))


#: 单说时间的一截（「明天早上八点」「下午三点」「十分钟后」）：只由时间词、时刻、日子、时段与虚词组成。
_TIME_PIECE = (rf"(?:{_FUTURE_DAY_ALT}|{DAY_ALT}|{SEG_ALT}|{_LATER_WORD}|(?:下个?|这个?|本)?(?:周|星期|礼拜)[一二三四五六日天末]?"
               rf"|{CN_NUM_SRC}\s*(?:号|日|点钟?|点半|点|分钟?|刻|个?小时|钟头|秒钟?)"
               r"|半|整|左右|大概|大约|差不多|准时|以后|之后|后|前|的|了|在|到|等|过)")
_TIME_ONLY_RE = re.compile(rf"(?:{_TIME_PIECE}|\s)+", re.IGNORECASE)
_SUBORDINATE_LATER_RE = re.compile(r".+(?:的?时候|以后|之后)$")


def _time_only_clause(clause: str) -> bool:
    """这一截只说了「什么时候」：时间从句（「下班的时候」「到家以后」）或纯时间（「明天早上八点」）。"""
    c = str(clause or "").strip()
    if not c:
        return False
    if _SUBORDINATE_LATER_RE.fullmatch(c) and (_has_later_when(c) or not c.endswith("时候")):
        return True
    return bool(_TIME_ONLY_RE.fullmatch(c)) and defers_to_later(c)


_VAGUE_LATER_RE = re.compile(_VAGUE_LATER)
_THEN_RE = re.compile(r"再(?!见)")


def as_reminder_request(text) -> str:
    """往后推的说法改说成一句提醒请求（拦光之后收尾卡上「到时候提醒我」那一项发它，`PlanBuilder._deferred_offer`）。

    提醒认「X 提醒我 Y」：「十分钟后提醒我打开空调」建 10 分钟后的「打开空调」，「到了公司提醒我开空调」是位置提醒。所以「X 再 Y」里 X 只说了
    时刻 / 时长 / 到了哪儿时改成「X提醒我Y」；X 是「等会儿」这类说不出时刻的词，或者「再」在要做的事里面（「八点把空调再调高一点」）时，
    「提醒我」加在原话前（「提醒我八点打开空调」）——时间词提醒自己剥，「等会儿」没有时刻它会追问。"""
    said = re.sub(r"\s+", "", str(text or "")).strip("，,。.！!？?～~")
    m = _THEN_RE.search(said)
    if m:
        head, tail = said[:m.start()], said[m.end():]
        if (head and tail and not _VAGUE_LATER_RE.fullmatch(head)
                and (_LATER_THEN_RE.fullmatch(head + "再") or _ARRIVE_THEN_RE.fullmatch(head + "再")
                     or _time_only_clause(head))):
            return f"{head}提醒我{tail}"
    return f"提醒我{said}"


def _leads_with_time_clause(text: str) -> bool:
    """句首单说时间、逗号后才是要做的事（「明天早上八点，帮我打开空调」「下班的时候，打开空调」）：后面那一截本身不带时间，
    端侧逐段判会漏，整句上云。"""
    parts = re.split(r"[，,；;]", str(text or "").strip(), maxsplit=1)
    return len(parts) == 2 and bool(parts[1].strip()) and _time_only_clause(parts[0])


def is_deferred_instruction(text) -> bool:
    """Any part of this utterance may wait on a condition, so nothing may run before it is judged.

    端侧用：拿不准就整句上云。往后推的说法不在这里整句判——端侧在分类出口逐段否决（`fast_intent.classify_structured`
    用 `defers_to_later`），「音量调小一点，提醒我八点开会」的音量那段照常本地秒回；这里只收两种逐段判不出来的：
    句首单说时间的一截（`_leads_with_time_clause`）与「X 了再 Y」（云端照常规划）。"""
    t = str(text or "")
    return bool(DEFERRED_CONDITION_RE.search(t) or _has_bare_condition(t) or _DONE_THEN_RE.search(t)
                or _leads_with_time_clause(t))


#: 「只要评分四点五以上的」「只要靠窗的」：这里的「只要」是「只想要」——后面是一个「…的」名词短语、没有后件，不等任何条件
#: （核心旅程 NB06：被当成条件句升成多轮，循环里补了一步不带目标的详情，挂起反问时把列表卡丢了）。只认「只要」起的这一截
#: 直到句末（或空白——规划器把目标接在空白后面）都没有分句、没有就 / 便 / 才 / 都 / 则 / 的话、以「的」收尾（「就行 / 就好」
#: 剥掉）；后件说出来了的（「只要评分四点五以上的，就导航到第一家」「只要下雨就关天窗」「只要下雨关天窗」）照旧是条件句。
#: 2026-10-13 量过：条件反例 25 / 25 仍判条件，「…的」收尾的「只想要」15 / 15 认出（docs/design/2026-10-13-v2-200x5-followups.md §13）。
_ONLY_WANT_RE = re.compile(
    r"只要(?:(?!的话)[^，,。；;！!？?\s就便才都则]){1,30}?的(?:就行|就好|就可以|就够了)?[了吧呀啊呢嘛。！!]*(?=\s|$)")


def waits_on_condition(text) -> bool:
    """云端判「这句话（或规划目标）要不要等条件」：同一条延后条件判据，只是先去掉「只想要」义的「只要…的」。

    端侧「不在本地执行」照旧用 `is_deferred_instruction`，任何「只要」都上云：拿不准就交给规划器——「车窗只要开左边的」
    这类带挑选的说法不该在端侧就地执行。往后推的说法（`defers_to_later`）不在这里：它们不升多轮、不等前件判定，
    只是当场的端侧车控不做。"""
    t = _ONLY_WANT_RE.sub("", str(text or ""))
    return bool(DEFERRED_CONDITION_RE.search(t) or _has_bare_condition(t))


#: 以假设框架起句、条件和后件都说出来的条件指令（「如果深圳今天下雪，就帮我把空调打开」）。后件做不做取决于前件，前件要看过观察
#: 才判得了；规划器会升成多轮，但再规划不会可靠地判断前件——2026-10-12 看到「今天不会下雪」仍派出开空调（核心旅程 D02 3/3）。
#: 云端问句闸据此不执行后件的副作用：不带「帮我 / 请」的说法整句已按提问拦，带了的（以及以「若」起句的）靠这一条（D04）。
#: 只认起句的框架：「打开空调，要是太冷再关掉」前一半是当下就要做的事，不在此列。
_LEADING_CONDITION_RE = re.compile(
    r"^(?:那么?|那就|嗯|你)?(?:如果|要是|假如|若|万一|假设)(?:说)?"
    r"(?P<condition>[^，,。；;！？!?\n]{1,40}?)"
    r"(?:的话|就|则|那么?|[，,])"  # 不收「便」：会把「如果方便的话」切成条件「方」
)
#: 只是客气、不是外部条件：「如果可以的话，帮我把空调打开」「要是方便的话帮我开下窗」「要是你能的话就把空调打开吧」。
_POLITE_CONDITION_RE = re.compile(r"(?:你|您)?(?:可以|方便|能|愿意|有空|不麻烦|不介意|允许|行|没问题)(?:的话)?")
#: 后件只是「就行 / 就好了」：说的是做到这样就够，不是另一件要等条件的事（「如果你能把空调打开就好了」）。
_NO_CONSEQUENT_RE = re.compile(r"(?:就|那就|那|则)?(?:行|好|可以|够了?|OK|ok)?[了啊吧呀呢的。！!？?…~]*")


def split_conditional_instruction(text) -> tuple[str, str] | None:
    """以假设框架（或没有框架词的「X 的话」）起句、说出了真条件和后件 ⇒ (条件, 后件)；不是条件指令 ⇒ None。

    后件保留原话（含「就 / 帮我」这类连接与客气成分），由用它的地方自己清理：T2 的前件判定拿条件去问、拿后件判哪几步是用户
    点名要做的（`runtime.condition_judgement`，设计 docs/design/2026-10-12-conditional-instruction-judgement.md）。"""
    t = re.sub(r"\s+", "", str(text or ""))
    m = _LEADING_CONDITION_RE.match(t)
    if m:
        if _POLITE_CONDITION_RE.fullmatch(m.group("condition")):
            return None
        condition = m.group("condition")
    else:
        m = _LEADING_BARE_IF_RE.match(t)
        condition = _real_condition(m.group("condition")) if m else ""
        if not condition:
            return None
    consequent = t[m.end():].lstrip("，,、：:")
    if not consequent or _NO_CONSEQUENT_RE.fullmatch(consequent):
        return None
    return condition, consequent


def is_conditional_instruction(text) -> bool:
    """以假设框架起句、说出了真条件和后件：后件要等条件判过才能做（判不了就不做）。"""
    return split_conditional_instruction(text) is not None
