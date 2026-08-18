"""候选集上的聚合问答——**确定性、零 LLM**（QA Q2 残余，2026-08-19）。

## 为什么它必须是确定性的，而不是「给 Planner 多一点上下文」

I-018「哪家最晚关门」、I-023「已知两项价格却不算合计」。这两句的答案**已经在系统
手里**（`Focus.candidate_sets` 的结构化 items），需要的是算，不是想。

而它不该交给任何 Agent，因为**落到哪个 Agent 都是错的**：
`nearby.search` 会重搜一遍答**新一批**（真栈实测 CD1 首跑「逐字重复上一轮整段列表」
就是这个形态）、`nearby.detail` 只看一个、chitchat 手里根本没有那些数只能编。
⇒ 所以拦在**落域之前**：同 I-052 那条守卫的位置，只是方向相反——
那条是「引用了候选但一份都没有 ⇒ 确定性弃权」，这条是
「引用了候选而候选就在手里 ⇒ 确定性回答」。**一正一反，同一个判据面。**

> 判据来源：`runtime/clock.py` 那条「系统持有的事实绝不让 LLM 答」，
> 以及 Q6 `agents/chitchat/src/audit.py` 建好的形态（确定性 handler 回答系统事实）。
> 直接证据是零方差：同一份候选集，三次取样话术逐字相同。

## 判据为什么取窄（三段同时命中）

这条短路看到的是**全部流量**，误伤代价是「整轮不进 Planner」，比 chitchat 兜底那条
高一档。所以要求三件事同时成立，宁可漏说法也不误伤：

1. **引用当前候选**（`哪家`/`哪个`/`这几家`/`第 N 个`…）——「最晚关门的呢」这种
   没有指示词的说法**刻意不劫持**，照常进 Planner。同「标反方向比漏标贵」。
2. **算子 + 维度**（一张表，`最晚关门`→关门/取最大，`最便宜`→价格/取最小…）。
   算子词本身就编码了维度，所以两者一起匹配而不是各匹一次——分开匹会让
   「最近的加油站」（新搜索）命中「距离/最小」。
3. **没有新搜索指示词**（`附近`/`周边`/`帮我找`/`有没有`…）——「附近最便宜的加油站」
   是一次新检索，不是对当前列表的聚合。

## 数据不全时也是确定性回答，不是回落 LLM

一份候选里没有任何营业时间 ⇒ 诚实说「这份列表没带营业时间」，**不返回 None**。
返回 None 就等于把它交回 LLM 去编，而那正是 I-018 的病（「称全部未查到」是
系统在没数据时说的话，本身没错；错的是有数据时也这么说，以及没数据时编一条出来）。
"""
from __future__ import annotations

import re

from runtime import openhours
from runtime.cntime import CN_NUM_SRC, cn_int

#: 「在说当前这份候选」的**词表通道**。另有一条更硬的**名字通道**见
#: `_has_reference`——用户直接说出候选项的名字，那是比任何指示词都强的引用信号。
_REFERENCE_RE = re.compile(
    rf"哪家|哪个|哪一个|哪间|哪条|这些|那些|其中|"
    rf"(?:这|那)\s*(?:几|{CN_NUM_SRC})\s*(?:个|家|项|条|种|款|杯|份)|"
    rf"第\s*{CN_NUM_SRC}\s*(?:个|家|项|条|种|款|杯|份)")
#: 命中即**放行给 Planner**：这是一次新检索，不是对当前列表的聚合。
#: ⚠ 刻意不含「最近」——「最近的一家」既可能是新搜索也可能是问当前列表，
#: 交给 `_REFERENCE_RE` 去分（「附近最近的」有「附近」，会被这里挡住）。
_NEW_SEARCH_RE = re.compile(
    r"附近|周边|就近|旁边|这边|哪里有|哪儿有|有没有|帮我找|帮我搜|再搜|换一批|重新搜")

#: `(算子正则, 维度, 取最大?)`。**按声明序求值，第一条命中即用**——同
#: `retry_policy.py` 的表驱动纪律：加一种问法=加一行，不改主循环。
_SUPERLATIVES: tuple[tuple[re.Pattern[str], str, bool], ...] = (
    (re.compile(r"最晚(?:关|打烊|营业|结束)|关(?:门|店)最晚|营业最晚|最迟(?:关|打烊)"),
     "closing", True),
    (re.compile(r"最早(?:关|打烊|结束)|关(?:门|店)最早"), "closing", False),
    (re.compile(r"最(?:便宜|低价|划算)|价(?:格|钱)最低|最省钱"), "price", False),
    (re.compile(r"最贵|价(?:格|钱)最高"), "price", True),
    (re.compile(r"评分最高|最高分|分最高|口碑最好|评价最好"), "rating", True),
    (re.compile(r"评分最低|最低分|分最低|评价最差"), "rating", False),
    (re.compile(r"最近|离(?:我|这)?最近|距离最近"), "distance", False),
    (re.compile(r"最远|距离最远"), "distance", True),
)
#: 合计型。**必须有合计词**——「巨无霸多少钱」是单品查询不是求和。
_TOTAL_RE = re.compile(r"一共|总共|共计|合计|加起来|加一起|加在一起|总价|总额|总金额")
_TOTAL_DIM_RE = re.compile(r"钱|价|费|花|多少")

#: 维度的人话名与单位。**话术里的量词也在这张表里**，不散在各分支。
_DIM_LABEL = {"closing": ("营业到", ""), "price": ("", " 元"),
              "rating": ("评分 ", ""), "distance": ("", " 公里")}
_DIM_NOUN = {"closing": "营业时间", "price": "价格",
             "rating": "评分", "distance": "距离"}

_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")
#: 序数并列：「第一个和第二个」「第 1 个、第 3 个」。
_ORDINAL_RE = re.compile(rf"第\s*({CN_NUM_SRC})\s*(?:个|家|项|条|种|款|杯|份)")


def _numeric(raw) -> float | None:
    """从候选项字段取一个数。`0` 视为缺失——`rating`/`distance_km` 的默认值就是 0，
    让它参与排序会让「数据缺失」赢下「最低评分」（同 `last_places_ts=0` 的口径）。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw) or None
    m = _NUM_RE.search(str(raw or ""))
    if not m:
        return None
    try:
        return float(m.group(1)) or None
    except ValueError:
        return None


def dimension_value(item: dict, dim: str) -> float | None:
    """候选项在某个维度上的可比较取值。判不出 → None（**不是 0**）。

    字段名**从产生方派生**：`nearby._item()` 出 `open_today`/`rating`/`cost`/
    `distance_km`，商户 `_menu_item()` 出 `price`。白名单
    `context._CANDIDATE_ITEM_KEYS` 与这里是同一份契约的两端。
    """
    if not isinstance(item, dict):
        return None
    if dim == "closing":
        got = openhours.closing_minute(item.get("open_today"), item.get("open_week"))
        return float(got) if got is not None else None
    if dim == "price":
        # `price`（商户单品价）优于 `cost`（nearby 人均）——前者是这一项本身的价钱。
        return _numeric(item.get("price")) or _numeric(item.get("cost"))
    if dim == "rating":
        return _numeric(item.get("rating"))
    if dim == "distance":
        return _numeric(item.get("distance_km"))
    return None


def _render(dim: str, value: float) -> str:
    if dim == "closing":
        return f"营业到 {openhours.format_minute(int(value))}"
    prefix, suffix = _DIM_LABEL[dim]
    body = f"{value:.2f}".rstrip("0").rstrip(".") if dim != "rating" else f"{value:g}"
    return f"{prefix}{body}{suffix}"


def _ordinals(text: str) -> list[int]:
    """句子里点名的序数，去重保序。越界与 0 由调用方按 items 长度过滤。"""
    out: list[int] = []
    for raw in _ORDINAL_RE.findall(text):
        n = cn_int(raw)
        if n and n not in out:
            out.append(n)
    return out


def _named(text: str, items: list[dict]) -> list[dict]:
    """句子里按**名字**点到的候选项，按候选集顺序。

    I-023 的原话是「巨无霸 26.50、可乐 9.50，问总价」——用户说的是名字不是序数，
    所以名字通道是必需的。**只认 ≥2 字的名字**：单字名会把「可」「乐」这类
    误配进来（同 `mcdonalds._menu_matches` 那条反向包含的 2 字下限）。

    ⚠ **被其他命中项包含的名字要剔掉。** 菜单里「拿铁」与「生椰拿铁」并存时，
    一句「生椰拿铁和美式一共多少钱」会让裸子串匹配命中**三项**，于是合计多算一杯
    ——**一个算错的确定性答案比不答更糟**，它看起来权威。剔除后不足两项就交回
    Planner（保守方向：宁可不答，不算错）。
    """
    hit = []
    for item in items:
        name = str((item or {}).get("name") or "").strip()
        if len(name) >= 2 and name in text:
            hit.append(item)
    names = [str(it.get("name") or "") for it in hit]
    return [it for it in hit
            if not any(other != str(it.get("name") or "")
                       and str(it.get("name") or "") in other
                       for other in names)]


def _superlative_answer(text: str, items: list[dict]) -> str | None:
    for pattern, dim, want_max in _SUPERLATIVES:
        if not pattern.search(text):
            continue
        ranked = [(v, str(it.get("name") or ""))
                  for it in items
                  if (v := dimension_value(it, dim)) is not None and it.get("name")]
        if not ranked:
            # 诚实弃权，**确定性**。回落 LLM 只会得到一条编出来的记录（I-052）。
            return f"刚才那份列表里没带{_DIM_NOUN[dim]}信息，这个我算不出来。"
        best = max(v for v, _ in ranked) if want_max else min(v for v, _ in ranked)
        winners = [n for v, n in ranked if v == best]
        head = "、".join(f"「{n}」" for n in winners)
        tail = ("" if len(ranked) == len(items)
                else f"（这份列表里有 {len(ranked)}/{len(items)} 家带{_DIM_NOUN[dim]}）")
        return f"{head}{_render(dim, best)}{tail}。"
    return None


def _total_answer(text: str, items: list[dict]) -> str | None:
    if not (_TOTAL_RE.search(text) and _TOTAL_DIM_RE.search(text)):
        return None
    picked = [items[i - 1] for i in _ordinals(text) if 0 < i <= len(items)]
    if not picked:
        picked = _named(text, items)
    if len(picked) < 2:
        # 点不到两项就没有「合计」可言。**这里回落 Planner 而不是诚实弃权**：
        # 「一共多少钱」也可能问的是一个购物车/一张订单，那不是本模块的事。
        return None
    priced = [(str(it.get("name") or ""), v) for it in picked
              if (v := dimension_value(it, "price")) is not None]
    if len(priced) < len(picked):
        missing = [str(it.get("name") or "") for it in picked
                   if dimension_value(it, "price") is None]
        return ("这几项里 " + "、".join(f"「{n}」" for n in missing if n)
                + " 没有价格，合计算不出来。")
    total = round(sum(v for _, v in priced), 2)
    parts = " + ".join(f"{n} {v:.2f}" for n, v in priced)
    return f"{parts}，一共 {total:.2f} 元。"


def _has_reference(text: str, items: list[dict]) -> bool:
    """这句话在引用当前那份候选吗。两条通道取或：

    · **词表通道**：`哪家`/`这两个`/`第 N 个`…
    · **名字通道**：句子里点到了 ≥2 个候选项的名字。I-023 的原话就是名字
      （「巨无霸 26.50、可乐 9.50，问总价」），没有任何指示词——**说出名字比说
      「这两个」更明确地指向那份列表**。而且这条通道是从候选集派生的，
      不是又一张词表，所以它不会因为用户换个说法就失效。
    """
    return bool(_REFERENCE_RE.search(text)) or len(_named(text, items)) >= 2


def is_candidate_aggregate_question(text: str,
                                    items: list[dict] | None = None) -> bool:
    """这句话是不是「对当前候选集做聚合」。**确定性纯函数、零 LLM、零网络。**

    三段同时成立才算（引用当前候选 / 算子+维度 / 不是新检索）。
    `items` 缺省时只走词表通道——名字通道要有候选集才判得了。
    """
    t = str(text or "").strip()
    if not t or _NEW_SEARCH_RE.search(t):
        return False
    if not _has_reference(t, [it for it in (items or []) if isinstance(it, dict)]):
        return False
    if any(pattern.search(t) for pattern, _, _ in _SUPERLATIVES):
        return True
    return bool(_TOTAL_RE.search(t) and _TOTAL_DIM_RE.search(t))


def answer(text: str, entry: dict | None) -> str | None:
    """→ 确定性话术，或 None（=不劫持，照常进 Planner）。

    `entry` 是 `context.newest_candidate_set(focus, allow_fallback=True)` 的返回
    ——**候选集该绑哪一组的口径只有一份**，这里不发明第二套。
    """
    items = [it for it in ((entry or {}).get("items") or []) if isinstance(it, dict)]
    if len(items) < 2:
        # 一项都没有 / 只有一项时没有「哪家最…」可比。零候选那一支由 I-052
        # 那条守卫兜（它管的是「引用了却没有」），这里只是不劫持。
        return None
    if not is_candidate_aggregate_question(text, items):
        return None
    t = str(text).strip()
    return _total_answer(t, items) or _superlative_answer(t, items)
