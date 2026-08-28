"""ActionabilityClassifier：裸对象澄清族的**第四条路**（B6 §2，shadow 档）。

## 为什么是形态判定，不是又一层检索式知识

「华润大厦」「上海」这类**裸对象**该澄清而不澄清，前三条修法全部走完并留了尸检
（AGENTS.md §4.2 专条、findings §18/§19/§25）：

- 写 guide 讲判据 → 四次实测 4/10 → 1/10，退回后回到 7/10（p≈0.02，**有害**）；
- 等量换出预选池 → 做完全量验证后由泓舟裁定回退（总分没变好，代价是冻结 baseline）；
- 给范例 schema 加 clarify 型 → 机制建成，但**治不了本族**：裸专名之间 IDF-Dice
  **全 0.000**。

第三条的失败恰恰给出了正解：**检索是内容通道，而「裸对象」是形态判据**。
「华润大厦」和「上海」一个字都不共享，所以任何按内容检索的东西都够不着它们；
可它们共享一个**形式**——整句里没有任何谓述或疑问的语法标记。本模块量的就是这个。

## 特征是语法标记，不是领域词表

§2.3 的纪律原文：「特征必须是**句法/形态量**（词性、槽完整度、焦点态），不是字面
模式表；出现『给某个专名写特判』的冲动时，那是范例/等价类的活。」

下面的词表全部是**封闭虚词类**（疑问词、语气词、介词、体标记、否定、连词、时间指示），
没有一个是领域实词。这一点由 `test_actionability.py` 的源码级断言钉死：任一标记若
与 VAL 对象名 / 能力 intent / 对象中文名撞上，测试当场红。

## shadow 铁律

本模块**不进主链**：`PlanBuilder.build` 只把判定写进 `plan.actionability` 供
`cloud.planning` span 归因，不影响任何决策。它的全部价值就是不生效——直到
分歧样本的对照实验证明它该接管（那是 canary，B6 §5 第 4 条明写要泓舟单独拍板）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from runtime.polarity import NEG_WORDS


class Actionability(str, Enum):
    EXECUTE = "execute"
    CLARIFY = "clarify"
    REJECT = "reject"


# ── 封闭虚词类：谓述/疑问的语法标记 ────────────────────────────────────────
#
# 判据不是「这个词什么意思」，而是「这个形式要求句子里有个谓词」。
# 例：「到」是趋向介词，它必须依附一个动词；裸专名后面不会跟「到」。

_INTERROGATIVE = (           # 疑问词与句末语气词
    "吗", "呢", "吧", "么", "什么", "啥", "怎么", "怎样", "咋", "如何",
    "哪", "几", "多少", "为什么", "是不是", "有没有", "谁", "?", "？",
)
_PREPOSITION = (             # 趋向/处置/受事介词——必须依附谓词
    "到", "去", "往", "向", "把", "被", "让", "从", "跟", "对",
)
_ASPECT = (                  # 体标记与动补
    "一下", "着", "过", "起来", "一点", "了",
)
_REQUEST = (                 # 祈使/请求标记（对助手说话的形式）
    "请", "麻烦", "帮", "给我", "替我", "我要", "我想", "想要", "需要",
    "能不能", "可不可以", "可以吗",
)
_NEGATION = (                # 否定预设了一个被否定的谓词
    "不", "别", "没", "无需", "不用", "不要",
)
_CONNECTIVE = (              # 多小句 ⇒ 至少一个谓词
    "然后", "接着", "随后", "顺便", "并且", "而且", "以及", "再", "先",
)
_TEMPORAL = (                # 时间指示语——状语只修饰谓词
    "今天", "明天", "后天", "昨天", "现在", "待会", "等下", "马上",
    "几点", "later",
)

_PARTICLE = (                # 结构助词：定语/状语/补语标记，裸专名里不会出现
    "的", "地", "得",
)
# 谓词类词（B6 §2.2 的一号特征「有明确动作动词？」）。**它只装谓词，不装任何对象名**
# ——这一点由源码级断言钉死：任一条目若与 VAL 对象名 / 对象中文名 / 能力 intent 段撞上，
# `test_actionability.py` 当场红。开/关两族逐字沿用 `planning.py` 已在跑的
# `_EXPLICIT_OPEN_ACTION_RE` / `_EXPLICIT_CLOSE_ACTION_RE`，不另起一份声明。
_PREDICATE = (
    "打开", "开启", "开一下", "关闭", "关掉", "关上", "合上", "收起",
    "播放", "播报", "播", "放", "查", "找", "搜", "看", "听",
    # ⚠ 这里**没有**「导航」：它同时是 VAL 的对象名（`commands.yaml` display_name），
    # 写进来这套东西就从形态判据退化成对象特判了——源码级断言当场抓到并删掉的就是它。
    # 删掉零代价：真实导航句必带趋向介词或别的谓词（「导航**到**」「导航**去**」
    # 「导航**回**家」），本类里已有的「到/去/回」全都接得住。
    "去", "回", "带我", "设置", "设", "调", "开", "关",
    "订", "买", "付", "发", "打", "提醒", "取消", "创建", "记住", "删除",
    "增加", "减少", "升", "降", "换", "讲", "说", "问", "告诉", "推荐",
)
# 系词/存在/心理谓词：「有点热」「车里有点闷」这类没有动作动词却仍是完整陈述的句子，
# 靠的是这一类；裸专名同样不含它们。
_COPULA = ("有", "是", "在", "像", "觉得", "感觉", "想", "要", "能", "会")
_NUMERAL = tuple("0123456789一二三四五六七八九十两半零") + ("第",)  # 数词与序数

# 动词重叠（AA / ABAB 的 AA 面）：现代汉语里最可靠的**形态**动词标志之一
# ——「看看」「讲讲」「找找」「试试」。它不认识任何具体动词。
_REDUPLICATION_RE = re.compile(r"([一-鿿])\1")

# 条件从句标记（C6-C，2026-08-28，QA P1-03/T54）。封闭虚词类，一个实词都没有。
_CONDITIONAL = ("如果", "要是", "假如", "假设", "万一", "若是", "倘若", "的话", "一旦")
# 禁止式否定——**「别做 X」而不是「X 没发生」**。词表逐字复用 `runtime.polarity.NEG_WORDS`
# （极性判据的唯一声明处），这里只把它拆成元组用于形态计数，不另起一份表。
_PROHIBITIVE = tuple(NEG_WORDS.removeprefix("(?:").removesuffix(")").split("|"))

_MARKER_CLASSES = {
    "interrogative": _INTERROGATIVE,
    "preposition": _PREPOSITION,
    "aspect": _ASPECT,
    "request": _REQUEST,
    "negation": _NEGATION,
    "connective": _CONNECTIVE,
    "temporal": _TEMPORAL,
    "particle": _PARTICLE,
    "numeral": _NUMERAL,
    "predicate": _PREDICATE,
    "copula": _COPULA,
    "conditional": _CONDITIONAL,
    "prohibitive": _PROHIBITIVE,
}

# 省略续问形态：整句只有指示/序数/替换，靠上一轮焦点才解释得通。
# 与 planning.py 的 `_FOCUS_DEPENDENT_ELLIPSIS_RE` 是同一族判据的**只读**版本。
_ELLIPSIS_RE = re.compile(
    r"^(?:那个|这个|它|他|她)?"
    r"(?:换一批|换一个|换个|下一个|上一个|第[一二三四五六七八九十\d]+个"
    r"|再来一批|另来一批|详情|继续|就它|就这个)$"
)

_PUNCT_RE = re.compile(r"[\s，,。；;！？!?、：:\"'“”‘’（）()]+")

# 裸对象的长度上界：中文实体名少有超过这个长度还不带任何语法标记的。
# **这是量而不是词表**——它不认识任何具体对象。
_BARE_MAX_CHARS = 14


@dataclass(frozen=True)
class ActionabilityFeatures:
    """全部可离线计算、不依赖检索、不依赖任何领域词汇。"""

    chars: int
    markers: tuple[str, ...]          # 命中的标记类名（不是标记原文——那是内容）
    has_focus: bool
    ellipsis_form: bool
    hands_free: bool

    @property
    def predicated(self) -> bool:
        return bool(self.markers)

    @property
    def conditional_constraint(self) -> bool:
        """条件从句 + **禁止式**否定 = 一句「怎么做事」的约束，不是本轮要做的事。

        真栈 T54：「如果找不到她的地点就直接问我，不要猜另一个城市」——整句没有任何
        本轮动作请求，planner 却产出了 `navigation.reroute` 加了个咖啡途经点。
        本模块此前对它恒判 EXECUTE（谓述标记一大把），**REJECT 声明了但 v1 不产出**
        的成本第一次在真栈兑现成误执行。

        判据取两条形态的**合取**，都是封闭虚词类：
        - 条件从句标记（如果/要是/的话…）；
        - **禁止式**否定（别/不要/不许…，`runtime.polarity.NEG_WORDS`）——
          不是普通否定。「如果明天**不**下雨就提醒我洗车」是真请求，用「不」；
          「**不要**猜另一个城市」是在约束助手自己怎么选。这一条把两者分开。

        ⚠ 已知误报面：「如果堵车就别走高速」这类**条件式动作请求**同样两条全中。
        本判定只进 shadow 观测面（`plan.actionability`），不进任何决策——先看
        真实分布里误报占多少（B6 §5：接管是 canary，要泓舟单独拍板）。
        """
        return "conditional" in self.markers and "prohibitive" in self.markers


@dataclass(frozen=True)
class ActionabilityVerdict:
    decision: Actionability
    confidence: float
    features: ActionabilityFeatures

    def as_attr(self) -> str:
        """span 归因用的紧凑串：`<decision>|<confidence>`（同 edge_nlu 口径）。"""
        return f"{self.decision.value}|{self.confidence:.2f}"


def extract_features(text: str, *, focus=None, input_source: str = "",
                     ) -> ActionabilityFeatures:
    normalized = _PUNCT_RE.sub("", str(text or ""))
    raw = str(text or "")
    markers = tuple(
        name for name, words in _MARKER_CLASSES.items()
        if any(word in raw for word in words)
    )
    if _REDUPLICATION_RE.search(normalized):
        markers += ("reduplication",)
    last_intent = str(getattr(focus, "last_intent", "") or "").strip()
    return ActionabilityFeatures(
        chars=len(normalized),
        markers=markers,
        has_focus=bool(last_intent),
        ellipsis_form=bool(_ELLIPSIS_RE.fullmatch(normalized)),
        hands_free=str(input_source or "").startswith("voice_"),
    )


def classify(text: str, *, focus=None, input_source: str = "",
             ) -> ActionabilityVerdict:
    """EXECUTE / CLARIFY / REJECT + confidence。

    判定只有一条主线：**整句零谓述标记且短** = 用户给了对象、没给动作。
    此时唯一能救它的是对话焦点（上一轮意图解释得了这个省略），否则该问一句
    而不是替用户挑一个动作。

    ⚠ REJECT **v1 不产出**，理由写在 `_reject_not_produced_in_v1`。
    """
    features = extract_features(text, focus=focus, input_source=input_source)
    if features.chars == 0:
        return ActionabilityVerdict(Actionability.CLARIFY, 0.5, features)
    if features.conditional_constraint:
        # C6-C：条件式约束陈述**排在谓述判定之前**——它谓述标记一大把，放在后面
        # 永远轮不到。置信度压在 0.7：两条形态的合取比「整句零谓述」弱，
        # 而形态判据永远不该自称确定。
        return ActionabilityVerdict(Actionability.CLARIFY, 0.7, features)
    if features.predicated or features.chars > _BARE_MAX_CHARS:
        # 有谓述标记 = 用户已经说了要做什么。置信度按命中的标记类数递增，
        # 封顶 0.95——形态判据永远不该自称确定。
        return ActionabilityVerdict(
            Actionability.EXECUTE, min(0.95, 0.6 + 0.1 * len(features.markers)),
            features)
    if features.has_focus:
        # 焦点解释得了这个省略——B6 §2.2 的三号特征。省略形态（「换一批」「第二个」）
        # 置信度更高；其余低信息续问（「确认」「就它」）只是**可能**被焦点解释，
        # 压低置信度但同样不反问：已经有活跃语境时再问一遍是打断，不是澄清。
        return ActionabilityVerdict(
            Actionability.EXECUTE, 0.8 if features.ellipsis_form else 0.6, features)
    return ActionabilityVerdict(Actionability.CLARIFY, 0.85, features)


def _reject_not_produced_in_v1() -> str:
    """REJECT 在枚举里但 v1 不产出——这是刻意的，不是漏了。

    拒识判的是**受话**（这句话是不是对助手说的），而受话与「有没有说清要做什么」
    是两个正交问题：hands-free 通道里旁人一句「明天去上海吧」形态上完全合法。
    要判它得有 hands-free 语料的分母，而 PoC 没有真实流量（同 P3b 放量卡的处境）。
    R4.4 已有一条走 LLM 的拒识路径在跑，先落一个恒不命中的分支只会是死代码，
    还会让 shadow 读数里凭空多一档没人产出的决策。**有真实 hands-free 分母时再开。**
    """
    return "reject is declared but not produced in v1; see docstring"
