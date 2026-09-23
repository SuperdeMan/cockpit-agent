"""「这句话是在问，还是在下指令」——**唯一实现**（2026-08-27 从端侧下沉）。

## 它挡的是什么

端侧分类器认的是「对象 × 动作词」，于是「这车的天窗最大能开多大」命中天窗 + 开
→ **真把天窗打开了**。这一类不是落域偏好问题：**用户根本没有下指令**，
行驶中被误开天窗是真实安全问题（对抗测试 `ei.noise.question-about-control`）。

否决面只盖**写操作**，不盖查询——「胎压是多少」「电量还有多少」「温度怎么样」都带
疑问词，要的正是那条确定性秒回，一刀切会把好用的一起砍掉。判据是
**这次提问会不会被执行成写操作**，不是「这句话像不像问句」。

## 为什么住在 runtime/

因为**云侧也需要同一条判据，而云侧镜像够不着 `orchestrator/edge`**
（`orchestrator/cloud/Dockerfile` 只 COPY cloud/security/observability/runtime/skills）。
2026-08-26 QA 的 P0-01 就是这条缝：端侧对「问句 + 写动作」有闸
（`fast_intent.classify_structured` 出口），而同一句「红色机油灯亮了怎么办」上云之后，
planner 把它规划成 `warning_light.close` 并**真的执行了**——云端没有对应物。
补闸时唯一正确的做法是把这份判据搬到两边都够得着的地方，
**不是在云侧抄第二份**（B1 `stream_state` 那条：判定抄两份正是那个 bug 的成因）。

判据本身**零领域词**：全是封闭虚词类（疑问尾词、能力问法、属性问法、方式问法、
假设框架、祈使标记）。这一点由 `runtime/tests/test_question_shape.py` 的源码级断言守——
它与 `actionability.py` 的「特征全是封闭虚词类」同一条纪律。
"""
from __future__ import annotations
import re

#: 疑问尾词。判据是**结尾**（先剥掉标点），不是「句中出现过问号」。
QUESTION_TAILS = ("吗", "呢", "吗?", "吗？", "呢?", "呢？", "?", "？")
#: 能力问法：问的是「能不能」，不是让你做。
CAPABILITY_ASKS = ("能不能", "可不可以", "会不会", "是不是", "支不支持", "行不行", "有没有")
#: 数量/属性疑问词：问的是**参数本身**，构不成指令 → 无条件否决写操作。
#: 「几档 / 几级 / 几种 / 多少档」（批 6 W19-c 真栈：座椅加热话题下「它有几档」被规划成 `seat.heating.on`
#: 并执行——「几」族数量疑问此前不在表里）。刻意只收档位 / 等级 / 种类三个封闭量词，不收「几个」
#: （「开几个车窗」是模糊的祈使，宁可不认）。
PROPERTY_ASKS = ("多大", "多高", "多宽", "多长", "多快", "多少", "多久", "多远",
                 "几档", "几级", "几种", "多少档")
#: 选择/位置疑问词。它们问的是“哪一个”，不是让系统当场操作。
CHOICE_ASKS = ("哪个", "哪种", "哪边", "哪侧", "哪儿", "哪里", "在哪")
#: 规范/注意事项/条件问法。仍只放封闭句法，不放轮胎、保养等领域对象。
#: 「什么条件 / 什么情况」（同一趟真栈：「它在什么条件下会自动关闭」——「什么时候」在 `MANNER_ASKS` 里要与
#: 操作动词配对，而这句带「关」，配对后仍算指令；条件问法是无条件的提问）。
REFERENCE_ASKS = ("什么要求", "有何要求", "注意什么", "需要注意什么", "什么条件", "什么情况")
#: 方式/原因疑问词：可以出现在祈使式里（「温度如何调高」要的是调、不是问怎么调），
#: 因此与 `OPERATION_VERBS` 配对判断——**带操作动词就仍算指令**。
#: 两处判据必须是同一条，否则同一句话在「让不让给天气查询」与「算不算提问」上
#: 会得到相反的结论。
MANNER_ASKS = ("怎么", "咋", "如何", "为什么", "为啥", "什么时候")
HYPOTHETICAL_FRAMES = ("要是", "如果", "假如", "万一", "假设")
#: 面向助手的祈使标记：带这些词的疑问句是**礼貌请求**（「能帮我关下车窗吗」），
#: 是指令不是提问。
DIRECTIVE_MARKERS = ("帮我", "帮忙", "给我", "替我", "麻烦", "请")
#: 礼貌尾词（评审 2026-09-19 §4「帮我把窗关上好吗 vs 关窗会影响通风吗——不能只看问号」）：
#: 跟在**祈使主体**后面的「…好吗 / 行吗 / 可以吗」是软化的请求，不是提问。只有主体本身是
#: 祈使形态（动词打头，或「把 / 将 + 对象 + 动词」）才算——「这样开下去行不行」「空气好吗」
#: 「确认可以吗」的主体都不是祈使，照旧当提问（判据见 `_polite_request`）。
POLITE_TAILS = ("好吗", "好么", "好不好", "行吗", "行么", "行不行",
                "可以吗", "可以么", "可不可以", "成吗", "成不成", "可好")
#: 操作动词。与 `MANNER_ASKS` 配对：「怎么把温度调高」带「调」⇒ 仍是指令。
OPERATION_VERBS = ("调", "设", "开", "关", "升", "降", "加", "减")
#: 提问前缀（评审二轮 R9，2026-09-22）：「请问车窗能不能打开」的「请」不是祈使标记——
#: 「请问」整体是在开一个问句。先剥掉它再看主体；`runtime.memory_read` 消费同一份。
ASK_PREFIXES = ("请问", "问一下", "问下", "想问一下", "想问", "请教一下", "请教")
#: 元请求动词（同一批）：「请告诉我怎么关闭空调」「给我讲讲天窗是怎么开的」请求的是**解释**，
#: 不是控车——「告诉我 / 说说 / 教我」+ 一个疑问框架 ⇒ 提问，哪怕带「请 / 帮我 / 给我」。
#: 判据两段都要：动词在句首（可带礼貌前缀 / 人称），且其后有疑问框架；「查完告诉我」在句尾、
#: 后面没有疑问框架，是交付要求，不在此列。零领域词。
EXPLAIN_REQUESTS = ("告诉我", "说说", "讲讲", "说一下", "讲一下", "说明一下", "解释一下", "解释",
                    "介绍一下", "介绍", "教我", "教教我", "科普一下")
#: 列举问法（评审三轮追加批 E，2026-09-23）：问的是「有哪些 / 是哪几个」，不是让系统操作。此前不在任何一类里，
#: 端侧本地复算「天窗有什么用」**打开天窗**、「空调有什么模式」开空调、「氛围灯有哪些颜色」开氛围灯、「有什么好看的
#: 电影」播视频——本模块第一段要挡的就是这种形态。配对规则见 `_enumeration_question`：列举词之后带操作动词仍算指令
#: （「空调有哪些模式，开个制冷」）。「没有什么 / 没有哪些」是否定陈述，不算。
ENUMERATION_ASKS = ("有哪些", "有什么", "哪些", "哪几", "哪部", "哪首")
#: 原因问法（同一批）：问的是「为什么 / 怎么回事」，要的是**解释**。它不参与写操作的问句判定（「为什么要把温度调那么高」
#: 的既有合同由 `MANNER_ASKS` 管），只供**查询**让路——端侧只有读数，「新能源车冬天续航为什么会下降」曾被答成「电量72%」。
REASON_ASKS = ("为什么", "为啥", "什么原因", "啥原因", "原因", "原理", "怎么回事", "咋回事",
               "怎么会", "咋会", "怎么这么", "咋这么", "怎么那么",
               # 追加批 F（F-4）：「空调怎么不出风」「车窗怎么没关上」——问的是怎么回事，不是怎么做
               "怎么不", "咋不", "怎么没", "咋没")
#: 求信息的请求（追加批 F，F-2）：要的是一段回答，不是一个动作——「推荐 / 介绍 / 讲讲 / 说说 / 科普 / 解释」开头
#: （可带礼貌前缀）。与问句、原因问、解释元请求合起来，是规划失败时「兜底谈话就是答案」的那一类（AR05 F09 的例外）。
INFO_REQUEST_VERBS = ("推荐", "介绍", "讲讲", "说说", "科普", "解释")

# 方法问句中的动作词。它们仍是零领域的句法词，不包含任何车辆对象；“对象在前/动作在前，
# 中间带怎么/如何”的形态由本模块统一判定，端侧与云侧共用。刻意不含“调高/调低”：
# 既有“温度如何调高”按祈使处理的合同不在本批扩大。
HOW_TO_ACTIONS = (
    "打开", "开启", "关闭", "关掉", "使用", "操作", "进入", "连接", "设置",
    "更换", "启动", "停用", "换", "开", "关",
)
_HOW_TO_ACTION_ALT = "|".join(sorted(map(re.escape, HOW_TO_ACTIONS), key=len,
                                      reverse=True))
_OBJECT_FIRST_HOW_TO_RE = re.compile(
    rf"^.+(?:怎么|咋|如何)(?:才|才能|可以|应该|要|去)?(?:{_HOW_TO_ACTION_ALT})"
    r"(?:一下|呢|啊|呀|吧|才行)?$"
)
_ACTION_FIRST_HOW_TO_RE = re.compile(
    rf"^(?:怎么|咋|如何)(?:才|才能|可以|应该|要|去)?(?:{_HOW_TO_ACTION_ALT}).+"
    r"(?:一下|呢|啊|呀|吧|才行)?$"
)
#: 评审二轮 R9（2026-09-22）：「怎么把车窗打开」「如何把空调关闭」——方式疑问词 + 「把 / 将」处置式
#: + 操作动作，是方法询问。manual-rag v2 曾把「怎么把」显式排除（「显式执行框架」），于是端侧直接
#: 执行成 window.open：**「把」是句法结构，不是授权证据**。礼貌执行句（「帮我把车窗关上好吗」）
#: 由祈使标记 / 礼貌尾 + 祈使主体照旧判成请求；调节类动词（调 / 设 / 升 / 降）的既有合同不动
#: （`HOW_TO_ACTIONS` 刻意不含它们）。
_BA_FRAME_HOW_TO_RE = re.compile(
    rf"^(?:怎么|怎样|咋|如何)(?:才|才能|可以|应该|要|去)?(?:把|将)[^，,。！？!?]+?"
    rf"(?:{_HOW_TO_ACTION_ALT})(?:一下|呢|啊|呀|吧|了|才行)?$"
)
_ASK_PREFIX_ALT = "|".join(sorted(map(re.escape, ASK_PREFIXES), key=len, reverse=True))
_ASK_PREFIX_RE = re.compile(rf"^(?:{_ASK_PREFIX_ALT})[，,]?\s*")
_EXPLAIN_ALT = "|".join(sorted(map(re.escape, EXPLAIN_REQUESTS), key=len, reverse=True))
#: 元请求：句首（礼貌前缀 / 人称 / 「能 / 能不能 / 可以」之后）就是解释动词，其后跟着疑问框架。
_EXPLAIN_REQUEST_RE = re.compile(
    rf"^(?:请|麻烦|帮我|帮忙|给我|替我|你|您|能|能不能|可以|可不可以|先|再)*\s*(?:{_EXPLAIN_ALT})"
    r"[，,]?\s*.{0,16}?(?:怎么|怎样|咋|如何|为什么|为啥|什么|哪个|哪种|哪里|哪边|几|多少|多久"
    r"|能不能|可不可以|是不是|有没有|会不会|支不支持)")
#: 「有 …」开头的两种列举问前面不能紧挨「没」（「没有什么问题」是陈述）。
_ENUMERATION_RE = re.compile("|".join(
    (rf"(?<!没){re.escape(word)}" if word.startswith("有") else re.escape(word))
    for word in sorted(ENUMERATION_ASKS, key=len, reverse=True)))
_OPERATION_CLASS = "".join(OPERATION_VERBS)
#: 带操作动词字、却不是在下指令的四种形态（追加批 F，F-4）：可能补语的否定式「关不上 / 打不开 / 调不动 / 降不下来」、
#: 正反问「开不开 / 关不关」、已然否定「没关上」、设备自己的行为「自己关掉 / 自动开启」——说的是做不到 / 没做成 /
#: 要不要 / 它自己怎么了，原因问句里它们不算操作动词。
_NOT_AN_OPERATION_RE = re.compile(
    rf"[{_OPERATION_CLASS}][^，,。！!？?\s不]?不[上开了动下掉起出进住{_OPERATION_CLASS}]|没[{_OPERATION_CLASS}]"
    rf"|(?:自己|自动|自行)[{_OPERATION_CLASS}]")   # 「调节不了 / 开启不了」：双字动词中间隔一个字
_INFO_REQUEST_ALT = "|".join(sorted(map(re.escape, INFO_REQUEST_VERBS), key=len, reverse=True))
_INFO_REQUEST_RE = re.compile(
    rf"^(?:请|麻烦|帮我|帮忙|给我|替我|你|您|能|能不能|可以|可不可以|再)*\s*(?:{_INFO_REQUEST_ALT})")


_POLITE_TAIL_ALT = "|".join(sorted(map(re.escape, POLITE_TAILS), key=len, reverse=True))
_POLITE_TAIL_RE = re.compile(rf"^(?P<body>.+?)[，,、\s]*(?:{_POLITE_TAIL_ALT})$")
_IMPERATIVE_VERB_ALT = "|".join(sorted(
    map(re.escape, set(OPERATION_VERBS) | set(HOW_TO_ACTIONS)), key=len, reverse=True))
#: 祈使主体：动词打头（可带礼貌前缀 / 「再 / 先」），或最多 3 字前缀后的「把 / 将 + 对象 + 动词」。
#: 动词不在句首又没有「把」框架（「这样开下去」「空调开到26度」）不算——那正是问句与请求
#: 分不开的形态，宁可少认一次请求。
_IMPERATIVE_BODY_RE = re.compile(
    rf"^(?:请|麻烦|帮我|帮忙|给我|替我)?\s*(?:再|先|也)?\s*"
    rf"(?:(?:.{{0,3}}?)(?:把|将)[^，,。！？!?]{{1,12}}?)?(?:{_IMPERATIVE_VERB_ALT})")


#: 祈使**开头**（批 5 W18，2026-09-20）：「把 / 将 + …」处置式，或「请 / 麻烦 / 帮忙 / 替我 + …」
#: 礼貌前缀。它回答的是「这句话是不是在下一条新指令」——补槽挂起下的换题判据消费它：
#: 路况挂起（「您想查询哪条路线的路况？」）曾把「把全车门解锁」整句当路线吞掉。
#: 零领域词：全是虚词框架；不要求动词在表里（`_IMPERATIVE_BODY_RE` 那条要动词，是给礼貌尾词用的）。
IMPERATIVE_OPENING_RE = re.compile(
    r"^(?:请|麻烦|帮我|帮忙|给我|替我)?\s*(?:再|先|也|都)?\s*(?:把|将)\S"
    r"|^(?:请|麻烦|帮忙|替我)\s*\S")


def is_imperative_opening(t: str | None) -> bool:
    """「把全车门解锁」「将空调调到26度」「请打开车窗」「麻烦关一下天窗」→ True；
    「走滨海大道」「深南大道」「晚上九点」→ False。"""
    return bool(IMPERATIVE_OPENING_RE.match((t or "").strip()))


def carries_operation_cue(t: str | None) -> bool:
    """原话自己带着「要系统操作」的句法证据：祈使开头、操作动词，或操作动作词（追加批 G）。

    消费方只有一处：云侧 `explicit_input_not_addressed`（模型第一轮判「不受话」、被重试一次）。第二轮被催出来的写车控 /
    需确认步，原话里没有这份证据就作废（「可以，已为您执行」→ 关双闪）；第二轮如实答「受话、零步」时，没有这份证据的
    原话（问候 / 附和）交谈话作答，有的（「把全车门解锁」）照旧诚实报失败。零领域词：全是本模块既有的词表。
    「我有点冷」这类隐式诉求没有句法证据——是已知代价（设计 §10）。
    """
    body = strip_ask_prefix(t)
    if not body:
        return False
    return bool(is_imperative_opening(body)
                or any(v in body for v in OPERATION_VERBS)
                or any(a in body for a in HOW_TO_ACTIONS))


def _polite_tail_body(t: str) -> str | None:
    """礼貌尾词前面的主体；没有礼貌尾词返回 None。"""
    cleaned = (t or "").strip().rstrip("。！!？?~ ")
    m = _POLITE_TAIL_RE.match(cleaned)
    return m.group("body").strip() if m else None


def _polite_request(t: str) -> bool:
    """「把车窗关上好吗」——礼貌尾词 + 祈使主体 ⇒ 请求，不是提问。"""
    body = _polite_tail_body(t)
    if body is None:
        return False
    # 主体取最后一个分句：「仪表灯亮着，这样开下去行不行」看的是「这样开下去」
    body = re.split(r"[，,；;。！？!?]", body)[-1].strip()
    if not body:
        return False
    if any(w in body for w in HYPOTHETICAL_FRAMES) or any(
            w in body for w in (*CAPABILITY_ASKS, *PROPERTY_ASKS, *CHOICE_ASKS, *REFERENCE_ASKS)):
        return False
    return bool(_IMPERATIVE_BODY_RE.match(body))


def _is_how_to_question(t: str) -> bool:
    """无标点 ASR 的操作方法问句：对象在前 / 动作在前 / 「怎么把对象 + 动作」三种形态。"""
    cleaned = (t or "").strip().rstrip("。！!？?~ ")
    return bool(
        _OBJECT_FIRST_HOW_TO_RE.fullmatch(cleaned)
        or _ACTION_FIRST_HOW_TO_RE.fullmatch(cleaned)
        or _BA_FRAME_HOW_TO_RE.fullmatch(cleaned)
    )


def strip_ask_prefix(t: str | None) -> str:
    """剥掉句首的提问前缀（「请问 / 问一下 / 想问」）；没有就原样返回。"""
    return _ASK_PREFIX_RE.sub("", (t or "").strip(), count=1)


def is_explanation_request(t: str | None) -> bool:
    """「请告诉我怎么关闭空调」「给我讲讲天窗是怎么开的」——请求解释，不是请求执行。"""
    return bool(_EXPLAIN_REQUEST_RE.match(strip_ask_prefix(t)))


def asks_for_reason(t: str | None) -> bool:
    """「续航为什么会下降」「胎压报警是怎么回事」「电量怎么这么低」——要的是解释，不是一个读数。

    只给**查询**的让路用（端侧 `classify_structured` 出口第五维）；「续航还有多少」「续航怎么样」不算。
    """
    body = strip_ask_prefix(t)
    return any(word in body for word in REASON_ASKS)


def is_information_request(t: str | None) -> bool:
    """要的是一段回答，不是一个动作：问句 / 原因问 / 解释元请求 /「推荐 · 介绍 · 讲讲…」开头（追加批 F，F-2）。

    只供规划失败后的终态判定用（兜底谈话是答案还是伪装）；「打开空调」「导航去公司」「明天八点提醒我开会」不算。
    """
    body = strip_ask_prefix(t)
    if not body:
        return False
    return bool(is_non_directive_question(body) or asks_for_reason(body)
                or is_explanation_request(body) or _INFO_REQUEST_RE.match(body))


def is_non_directive_question(t: str) -> bool:
    """这句话是在**问**，而不是在**下指令**。"""
    # 「请问…」的「请」不是祈使标记：先剥掉提问前缀，再按主体判（评审二轮 R9）。
    t = strip_ask_prefix(t or "")
    # 真实 ASR 常不带问号。“雨刮器怎么打开”若继续落入下方“疑问词+操作动词”旧档，
    # 会被端侧直接执行成 wiper.on。对象/动作的词序已经给出方法询问信号，先于礼貌
    # marker 判定；“帮我把”仍由祈使标记挡住。
    if _is_how_to_question(t):
        return True
    # 元请求「请告诉我怎么…」带着「请」，却是在要一段解释——排在祈使标记之前。
    if is_explanation_request(t):
        return True
    if any(w in t for w in DIRECTIVE_MARKERS):
        return False
    # 礼貌尾词 + 祈使主体（「把车窗关上好吗」）是请求；排在疑问尾词之前，否则被「吗」一刀切。
    # 主体不是祈使的礼貌尾词句（「这一家评价好不好」「空气好吗」）是 A-not-A 提问。
    if _polite_request(t):
        return False
    if _polite_tail_body(t):
        return True
    if t.rstrip("。！!.~ ").endswith(QUESTION_TAILS):
        return True
    if any(w in t for w in HYPOTHETICAL_FRAMES):
        return True
    if (any(w in t for w in CAPABILITY_ASKS)
            or any(w in t for w in PROPERTY_ASKS)
            or any(w in t for w in CHOICE_ASKS)
            or any(w in t for w in REFERENCE_ASKS)):
        return True
    if _enumeration_question(t):
        return True
    if _reason_question(t):
        return True
    return (any(w in t for w in MANNER_ASKS)
            and not any(v in t for v in OPERATION_VERBS))


def _reason_question(t: str) -> bool:
    """原因式问法，且问词**之后**没有操作动词（追加批 F，F-4）。

    「空调为什么不制冷」里「调」在问词之前（主语「空调」的一部分）、「天窗为什么关不上」里「关」是做不到的「关不上」
    ——按字、不看位置的配对曾把两句判成指令，端侧真执行了 `hvac.on` / `sunroof.close`。「为什么要把温度调那么高」
    「为什么不开空调」问词之后仍有操作动词，照旧落到下面的方式问法配对里算指令（被测试钉住的合同）。
    """
    positions = [t.find(word) for word in REASON_ASKS if word in t]
    if not positions:
        return False
    tail = _NOT_AN_OPERATION_RE.sub("", t[min(positions):])
    return not any(v in tail for v in OPERATION_VERBS)


def _enumeration_question(t: str) -> bool:
    """列举问，且列举词之后**另起的分句**里没有操作动词。

    配对只看列举词之后：列举问的主语在前（「空调有什么模式」），而操作动词按字匹配，「空调」的「调」会把
    整句误判成指令。列举词所在的分句是被问的名词短语——「座椅有哪些调节功能」「车窗有哪些开启方式」里的操作字是
    修饰语（第一版只看「之后」，这几句照样判成指令，端侧真执行了 `seat.on` / `window.open`）；只有后面另起的分句
    带操作动词才说明用户同时下了指令（「空调有哪些模式，开个制冷」「天窗有什么用，打开看看」）。
    """
    match = _ENUMERATION_RE.search(t)
    if not match:
        return False
    later = re.split(r"[，,；;。！!？?]", t[match.end():], maxsplit=1)
    tail = later[1] if len(later) > 1 else ""
    return not any(v in tail for v in OPERATION_VERBS)
