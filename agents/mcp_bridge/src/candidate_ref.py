"""文本入口 → 上一轮候选集里的那一项（QA 卡 Q10「双入口不等价」，2026-08-19）。

## 它要修的是什么

I-020：菜单卡上明明有巨无霸，**说**「点一份巨无霸」答商品不存在，**点**同一张卡上的
按钮就出正确预览。I-025①：当前筛选只剩生椰拿铁，说「第一杯大杯少冰」跳回美式列表。

取证把卡上的说法修正了一处：按钮路径并没有携带 store 三元组或 product_code
——`ui_card.options[].send_text` 仍是一句中文。首版把它规范化为
`在<门店>点一份<商品全名>`，因此两条入口真正的差别不是「结构化 vs 自然语言」，
而是**用词是不是商家的原名**：

| 入口 | `item_query` 实际值 | 下游 `_matching_products` |
|---|---|---|
| 点按钮 | `巨无霸套餐`（商家原名） | 精确命中一款 |
| 说话 | `巨无霸` / `第一个` / `第一杯` | 多命中或零命中 → 追问、跳列表 |

⇒ 首版把**规范名**作为收敛目标；本模块把用户说法确定性翻成它，翻译后两条入口
在桥里逐字同路。

## 2026-08-24 校正：名字不是身份

真栈 MC2 推翻了「规范名足够」这个前提：同一份麦当劳官方菜单里，多项商品的展示名
可以逐字相同。MiniMax 把「第一个」转述成该项规范名后，下游按名字在完整菜单里又
命中三项；系统明明知道用户点的是哪一项，却在跨 Agent 时把身份丢了。

所以 `*.menu` 投影现在额外保留**有界商品 id**，按钮发送
`在<门店>点第 N 个：<商品名>`，语音序数也走同一翻译链。id 不是客户端或 planner
给的权威值：Agent 入口先删除同名保留槽，只能由当前服务端候选项重新写入；商户工作流
还会重新读取当前门店菜单，并只在那份新鲜菜单里按 code 精确匹配。无效/过期/换店 id
一律不命中。nearby/POI 候选仍不下发 id，不能借候选集绕过位置 scope。

⇒ 收敛目标是**候选项身份**：名字唯一时规范名已经足够；名字不唯一时必须再带上
服务端持有的商品 id。两个入口走的仍是同一条结构化解析链。

## 三条通道的**优先级是判据不是顺序**

1. **原名**（规范化后相等）——按钮路径走的就是这条，命中即原样返回（幂等）。
2. **序数**（`第一杯`/`第 2 个`）——句里恰好一个序数且在范围内。
3. **唯一部分名**（用户的说法是某一项名字的子串，且**只命中一项**）。

命中多项一律返回 None，**交回商户既有的追问链**——它会出选项卡让用户点，
那是正确行为。这里宁可不翻译也不翻错：翻错等于系统替用户改了他点的东西，
而它没有任何「我不太确定」的信号（同 `candidate_query._named` 那条
「一个算错的确定性答案比不答更糟」）。

## 为什么不与 `candidate_query._ordinals` 合并

两者问的不是同一个问题，代价也不同：那边问「这**句话**点到了哪些项」（可以是多项，
用来求和），这边问「这个**槽值**指的是哪一项」（必须恰好一项，否则不动）。
`context._CANDIDATE_REFERENCE_RE` 头上那段已经写明仓库里的四条序数正则各自回答
另一个问题、刻意不合并；共享的是词法层（`runtime.cntime` 的中文数字），
判据留在各自这一层。
"""
from __future__ import annotations

import json
import logging
import re

from runtime.cntime import CN_NUM_SRC, cn_int

logger = logging.getLogger("mcp-bridge.candidate_ref")

#: 下发键名。写在这里而不是各消费点——它是与编排的契约（`docs/conventions.md` §9.28），
#: 抄成两份就会在改名那天只改一处。
META_KEY = "focus_candidate_set"
#: 只允许本模块从服务端下发候选写入；Agent 入口先删除 planner 同名槽。
RESERVED_ID_SLOT = "_candidate_ref_id"
_ID_MAX = 128

#: 序数量词。`杯`/`份` 是商户面才有的说法（「第一杯」），`家`/`个`/`款`/`项` 与
#: 云侧那两条同源。量词**可省**（「第二个」「第二」都算），但序数词本身不可省。
_ORDINAL_RE = re.compile(rf"第\s*({CN_NUM_SRC})\s*(?:个|家|项|条|种|款|杯|份)?")
#: **锚在槽值开头**——同 `context._CANDIDATE_REFERENCE_RE` 那条纪律，理由也一样：
#: 序数只有出现在开头时才是「在指代那份列表」。一个本来就叫「生椰拿铁第二杯半价」
#: 的商品，槽值里的「第二」是名字的一部分，不是序号。
_LEADING_ORDINAL_RE = re.compile(rf"^\s*{_ORDINAL_RE.pattern}")
#: **整值就是一句序数指代**。比 `_LEADING_ORDINAL_RE` 严一档，因为它服务的是
#: 更强的一个断言：「这个值被放错了槽」。允许「那个/这个」之类的前缀虚词。
_BARE_ORDINAL_RE = re.compile(
    rf"(?:刚才|那|这|那个|这个)?\s*第\s*{CN_NUM_SRC}\s*(?:个|家|项|条|种|款|杯|份)?")
#: 原话里的序数引用（**锚在整句里唯一出现**才用）。它是最后一条通道：
#: planner 有时**一个槽都不填**，于是前面两条都够不着。
_TEXT_ORDINAL_RE = re.compile(rf"第\s*({CN_NUM_SRC})\s*(?:个|家|项|条|种|款|杯|份)")

#: 名字比对的规范化。与 `luckin._normalized` 同式（只留中文/字母/数字），
#: 比 `mcdonalds._normalized` 的标点表更宽——**宽在这里是对的**：本模块的产物是
#: 商家原名，之后仍由各商户自己那份严格判据去选品，宽的一步不会放松严的那一步。
_KEEP_RE = re.compile(r"[^0-9A-Za-z一-鿿]+")


def _normalized(value) -> str:
    return _KEEP_RE.sub("", str(value or "")).lower()


def parse(meta) -> dict | None:
    """meta → 下发的候选集；缺失/损坏一律 None（**不猜**）。

    模型与客户端都写不到 `step.meta`，但「写不到」不等于「一定是好的」——
    滚动发布期可能收到旧形状。所以这里逐层校验，一处不对就整份丢弃。
    """
    raw = (meta or {}).get(META_KEY)
    if not raw:
        return None
    try:
        entry = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(entry, dict):
        return None
    items = []
    for item in (entry.get("items") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if name and index > 0:
            parsed_item = {"index": index, "name": name}
            raw_id = item.get("id")
            if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool):
                candidate_id = str(raw_id).strip()[:_ID_MAX]
                if candidate_id:
                    parsed_item["id"] = candidate_id
            items.append(parsed_item)
    if not items:
        return None
    return {"source_intent": str(entry.get("source_intent") or ""),
            "items": items}


def _belongs_to(entry: dict, namespace: str) -> bool:
    """这份候选是不是**本能力自己**产出的。

    归属判据只有 `source_intent` 的域前缀。没有它，「先查附近的瑞幸」之后一句
    「点第一个」会把一个高德 POI 名当成商品名塞进 `item_query`——那是**用户从没
    说过的商品**，比答不出来糟得多（同 §4.3「认不出就返回空，绝不回落到某一档」）。
    """
    source = str(entry.get("source_intent") or "")
    return bool(namespace) and source.split(".", 1)[0] == namespace


def _by_ordinal(value: str, items: list[dict]) -> dict | None:
    lead = _LEADING_ORDINAL_RE.match(value)
    if lead is None:
        return None
    return _ordinal_item(value, items, cn_int(lead.group(1)))


def _ordinal_item(text: str, items: list[dict], index) -> dict | None:
    found = {n for raw in _ORDINAL_RE.findall(text) if (n := cn_int(raw))}
    if len(found) != 1 or not index:
        # 多个 = 「第一个和第二个一共多少钱」，那是**聚合问句**不是选品，
        # 归云侧 `candidate_query`；这里不从两个里猜一个。
        return None
    hit = [item for item in items if item["index"] == index]
    return hit[0] if len(hit) == 1 else None


def is_bare_ordinal(value) -> bool:
    """这个槽值**整体**就是一句序数指代（「第七个」「第 2 杯」「第三」）。

    它是「这个值被放错了槽」的判据：一个纯序数短语不可能是分类名、门店名或商品名
    ——planner 把它填进哪个槽都改变不了它在指代候选列表里的第 N 项。
    """
    return bool(_BARE_ORDINAL_RE.fullmatch(str(value or "").strip()))


def _by_exact_name(value: str, items: list[dict]) -> dict | None:
    """按钮路径走的就是这条：`send_text` 里已经是商家原名，命中即幂等返回。"""
    needle = _normalized(value)
    if len(needle) < 2:
        return None
    hit = [item for item in items if _normalized(item["name"]) == needle]
    return hit[0] if len(hit) == 1 else None


def is_ambiguous_exact_name(value, meta, *, namespace: str) -> bool:
    """槽值逐字命中同一候选组里的多项。

    这是唯一允许「目标槽已有值仍回看原话序数」的情况：值本身已被证明不能唯一
    定位，而原话可能保留了 MiniMax 改写时丢掉的「第 N 个」。普通实质内容仍不覆盖。
    """
    needle = _normalized(value)
    items = live_items(meta, namespace=namespace)
    if len(needle) < 2 or items is None:
        return False
    return sum(_normalized(item["name"]) == needle for item in items) > 1


def _by_partial_name(value: str, items: list[dict]) -> dict | None:
    """用户的说法是某一项名字的子串，且**只命中一项**（「巨无霸」→「巨无霸套餐」）。

    ≥2 字下限：单字命中面太大（「可」会命中「可乐」「可颂」）。同
    `mcdonalds._menu_matches` 与 `candidate_query._named` 的同一条下限。
    """
    needle = _normalized(value)
    if len(needle) < 2:
        return None
    hit = [item for item in items if needle in _normalized(item["name"])]
    return hit[0] if len(hit) == 1 else None


def resolve(value, meta, *, namespace: str) -> dict | None:
    """槽值 + 下发候选集 → 候选项 `{"index","name","id"?}`；判不出返回 None。

    **确定性纯函数、零 LLM、零网络。**`namespace` 传本能力 intent 的域前缀
    （`mcd.order` → `mcd`）——归属判据从 intent 派生，不写第二份商户名单。

    三条通道按声明序求值，第一条命中即用：原名 → 序数 → 唯一部分名。
    原名排最前是判据不是习惯：一个**本来就叫「第二杯半价套餐」**的商品，
    序数通道会把它读成「第 2 项」。
    """
    text = str(value or "").strip()
    if not text:
        return None
    items = live_items(meta, namespace=namespace)
    if items is None:
        return None
    return (_by_exact_name(text, items)
            or _by_ordinal(text, items)
            or _by_partial_name(text, items))


def live_items(meta, *, namespace: str) -> list[dict] | None:
    """本能力当前能引用的候选项；没有或不属于本域时 None。"""
    entry = parse(meta)
    if entry is None or not _belongs_to(entry, namespace):
        return None
    return entry["items"]


def from_raw_text(raw_text, meta, *, namespace: str) -> dict | None:
    """原话里的序数引用 → 候选项。**最后一条通道**，判不出返回 None。

    存在的理由是真栈实测的第三种形态：planner 有时**一个槽都不填**
    （「麦当劳的第七个多少钱」原样出了整份菜单，与上一轮逐字重复）。
    前两条通道都读槽值，够不着这一种。

    ⇒ 这条通道兑现的是本步判据的**完整版**：不只「序数落到哪一项」不该让 LLM 数，
    **「序数该放进哪个槽」也不该由它决定**——真栈三次取样，同一句话它分别填进
    `item_query`、填进 `category`、和一个都不填。

    **只在整句恰好出现一次序数时生效**（多个 = 聚合问句，归云侧 `candidate_query`），
    且调用方只在目标槽为空，或槽值已被证明是**重复规范名**时才用它——普通实质
    内容绝不覆盖；重复名本身不能唯一定位，原话序数才是仍然存在的身份事实。
    """
    text = str(raw_text or "").strip()
    if not text:
        return None
    items = live_items(meta, namespace=namespace)
    if items is None:
        return None
    found = [n for raw in _TEXT_ORDINAL_RE.findall(text) if (n := cn_int(raw))]
    if len(set(found)) != 1:
        return None
    return _ordinal_item(text, items, found[0])
