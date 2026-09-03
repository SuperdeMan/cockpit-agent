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
PROPERTY_ASKS = ("多大", "多高", "多宽", "多长", "多快", "多少", "多久", "多远")
#: 方式/原因疑问词：可以出现在祈使式里（「温度如何调高」要的是调、不是问怎么调），
#: 因此与 `OPERATION_VERBS` 配对判断——**带操作动词就仍算指令**。
#: 两处判据必须是同一条，否则同一句话在「让不让给天气查询」与「算不算提问」上
#: 会得到相反的结论。
MANNER_ASKS = ("怎么", "咋", "如何", "为什么", "为啥", "什么时候")
HYPOTHETICAL_FRAMES = ("要是", "如果", "假如", "万一", "假设")
#: 面向助手的祈使标记：带这些词的疑问句是**礼貌请求**（「能帮我关下车窗吗」），
#: 是指令不是提问。
DIRECTIVE_MARKERS = ("帮我", "帮忙", "给我", "替我", "麻烦", "请")
#: 操作动词。与 `MANNER_ASKS` 配对：「怎么把温度调高」带「调」⇒ 仍是指令。
OPERATION_VERBS = ("调", "设", "开", "关", "升", "降", "加", "减")

# 方法问句中的动作词。它们仍是零领域的句法词，不包含任何车辆对象；“对象在前/动作在前，
# 中间带怎么/如何”的形态由本模块统一判定，端侧与云侧共用。刻意不含“调高/调低”：
# 既有“温度如何调高”按祈使处理的合同不在本批扩大。
HOW_TO_ACTIONS = (
    "打开", "开启", "关闭", "关掉", "使用", "操作", "进入", "连接", "设置",
    "更换", "启动", "停用", "开", "关",
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


def _is_how_to_question(t: str) -> bool:
    """无标点 ASR 的操作方法问句；显式“把/将”执行框架不在本形态内。"""
    cleaned = (t or "").strip().rstrip("。！!？?~ ")
    if "怎么把" in cleaned or "如何把" in cleaned or "咋把" in cleaned:
        return False
    return bool(
        _OBJECT_FIRST_HOW_TO_RE.fullmatch(cleaned)
        or _ACTION_FIRST_HOW_TO_RE.fullmatch(cleaned)
    )


def is_non_directive_question(t: str) -> bool:
    """这句话是在**问**，而不是在**下指令**。"""
    t = t or ""
    # 真实 ASR 常不带问号。“雨刮器怎么打开”若继续落入下方“疑问词+操作动词”旧档，
    # 会被端侧直接执行成 wiper.on。对象/动作的词序已经给出方法询问信号，先于礼貌
    # marker 判定；“帮我把/怎么把”仍由上面的显式执行框架挡住。
    if _is_how_to_question(t):
        return True
    if any(w in t for w in DIRECTIVE_MARKERS):
        return False
    if t.rstrip("。！!.~ ").endswith(QUESTION_TAILS):
        return True
    if any(w in t for w in HYPOTHETICAL_FRAMES):
        return True
    if any(w in t for w in CAPABILITY_ASKS) or any(w in t for w in PROPERTY_ASKS):
        return True
    return (any(w in t for w in MANNER_ASKS)
            and not any(v in t for v in OPERATION_VERBS))
