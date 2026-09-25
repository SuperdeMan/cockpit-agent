"""座位 / 区域词：词表镜像 + 唯一一份识别算法（评审四轮待办，2026-09-25）。

## 权威在哪

词表的权威是 `orchestrator/edge/knowledge/entities.yaml` 的 `positions` 节（中文 → 协议标识）：端侧 VAL 归一化、
场景目录校验都读它。这里的 `POSITIONS` 是它的**镜像**——云侧镜像够不着 `orchestrator/edge`，两边镜像都 COPY 了
`runtime`。`runtime/tests/test_positions.py` 逐条对账（词与标识都比），多一个、少一个、映射不同都红。
改词表：先改 `entities.yaml`，再改这里。

## 修前

三份各记各的：entities 22 个词、端侧规则 `fast_intent._POSITION_KEYWORDS` 12 个、云侧焦点 `context._POSITION_WORDS`
8 个。「打开后排左车窗」端侧只认出「后排」⇒ 两扇后窗都开；「前排右座椅加热」⇒ 前排两个座椅都加热；「右前车窗」
认不出位置 ⇒ 按缺省范围执行。云侧焦点同样把「后排左」记成「后排」，下一轮「关掉」按位置反向时关两扇。

## 识别算法只有这一份

`scan_positions`：从左往右，每个位置取从这里开始的**最长**词（正向最大匹配），互不重叠、按原话出现顺序、同一个词
只记一次。结果与词表顺序无关（同一起点上两个等长的不同词不可能同时匹配）。「副驾驶位」不会先撞上「驾驶位」
（主驾那一侧），「后排左」不会再多出一个「后排」。

紧跟在方向介词后面的侧向词说的是**方向**：「座椅向右侧调一点」「腿托往右侧调一些」里的「右侧」不是位置（飞书语料
8590 条里这一形态 5 条，全是侧向词；「往后排吹」这类座位词不在此列）。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

#: 与 `entities.yaml positions` 逐条相同（对账见模块 docstring）。值一律写成元组，多值词条（「前排」）就是多个标识。
POSITIONS: dict[str, tuple[str, ...]] = {
    "主驾": ("front_left",),
    "主驾驶": ("front_left",),
    "主驾位": ("front_left",),
    "驾驶位": ("front_left",),
    "左前": ("front_left",),
    "副驾": ("front_right",),
    "副驾位": ("front_right",),
    "副驾驶": ("front_right",),
    "副驾驶位": ("front_right",),
    "右前": ("front_right",),
    "前排": ("front_left", "front_right"),
    "前排左": ("front_left",),
    "前排右": ("front_right",),
    "后排": ("rear_left", "rear_right"),
    "后排左": ("rear_left",),
    "后排右": ("rear_right",),
    "左后": ("rear_left",),
    "右后": ("rear_right",),
    "后排中间": ("rear_center",),
    "全车": ("all",),
    "所有位置": ("all",),
    "左侧": ("left",),
    "右侧": ("right",),
}

#: 座位标识（区别于侧向 `left / right` 与全车 `all`）。
SEAT_IDS = frozenset({"front_left", "front_right", "rear_left", "rear_right", "rear_center"})
#: 侧向标识。
SIDE_IDS = frozenset({"left", "right"})
#: 方向介词：紧挨在侧向词前面时，那个侧向词说的是方向。
DIRECTION_MARKERS = ("往", "向", "朝")


def ids_of(word: str) -> tuple[str, ...]:
    """位置词 → 协议标识（不在词表里 ⇒ 空元组）。"""
    return POSITIONS.get(str(word or ""), ())


def position_spans(text: str, words: Iterable[str] | Mapping | None = None) -> list[tuple[int, str]]:
    """`text` 里全部位置词命中 `(起点, 词)`：正向最大匹配、互不重叠、按出现顺序、**含重复**。

    `words` 缺省用 `POSITIONS` 的词；VAL 传自己加载的那份 `entities.yaml`（同一份词表的权威）。
    方向介词后的侧向词整词跳过（不算位置，也不再从它中间认别的词）。"""
    t = str(text or "")
    vocabulary = sorted({str(w) for w in (POSITIONS if words is None else words) if w}, key=len, reverse=True)
    spans: list[tuple[int, str]] = []
    i = 0
    while i < len(t):
        for word in vocabulary:
            if t.startswith(word, i):
                side = bool(ids_of(word)) and set(ids_of(word)) <= SIDE_IDS
                if not (side and i > 0 and t[i - 1] in DIRECTION_MARKERS):
                    spans.append((i, word))
                i += len(word)
                break
        else:
            i += 1
    return spans


def scan_positions(text: str, words: Iterable[str] | Mapping | None = None) -> list[str]:
    """`text` 里的位置词：`position_spans` 按出现顺序、同一个词只记一次。"""
    found: list[str] = []
    for _, word in position_spans(text, words):
        if word not in found:
            found.append(word)
    return found


def distinct_positions(words: Iterable[str]) -> list[str]:
    """按序去掉**已经被前面的词覆盖**的位置词（标识集合是前面各词标识并集的子集）。

    云侧「关掉」按位置逐个反向（每个词一步）：同一个座位的两个说法（「副驾驶」「副驾」「右前」）各算一次，就是同一个动作
    执行两次。「前排」之后的「主驾」也被覆盖；反过来「主驾」在前、「前排」在后时两个都留（「前排」还多出副驾）。
    不在词表里的词原样保留、按字面去重（不猜它指哪个座位）。"""
    kept: list[str] = []
    covered: set[str] = set()
    for word in words:
        w = str(word or "").strip()
        if not w or w in kept:
            continue
        ids = ids_of(w)
        if ids and set(ids) <= covered:
            continue
        kept.append(w)
        covered.update(ids)
    return kept
