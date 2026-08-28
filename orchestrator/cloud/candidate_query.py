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
> 以及 Q6 建好的那个形态（确定性 handler 回答系统事实）——它 2026-08-28 起住在
> `runtime/session_facts.py`（C4-B，原 `agents/chitchat/src/audit.py`）。
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

#: 「这句话点名了哪一组」只有一份实现，住在 `context`（组标签是候选集自己的字段）。
#: 这里**只消费位置**，不再造第二条判据——同 `newest_candidate_set` 那条
#: 「候选集该绑哪一组的口径只有一份」。
from .context import label_hit

#: 「在说当前这份候选」的**词表通道**。另有一条更硬的**名字通道**见
#: `_has_reference`——用户直接说出候选项的名字，那是比任何指示词都强的引用信号。
_REFERENCE_RE = re.compile(
    rf"哪家|哪个|哪一个|哪间|哪条|这些|那些|其中|"
    rf"(?:这|那)\s*(?:几|{CN_NUM_SRC})\s*(?:个|家|项|条|种|款|杯|份)|"
    rf"第\s*{CN_NUM_SRC}\s*(?:个|家|项|条|种|款|杯|份)")
#: 命中即**放行给 Planner**：这是一次新检索，不是对当前列表的聚合。
#: ⚠ 刻意不含「最近」——「最近的一家」既可能是新搜索也可能是问当前列表，
#: 交给 `_REFERENCE_RE` 去分（「附近最近的」有「附近」，会被这里挡住）。
#:
#: **公开名 = 这份词表的唯一来源**（C3-B，2026-08-28）。此前 `engine._is_topic_change`
#: 另有一份「这是不是新请求」的判据各自演化，于是「附近的川菜馆」在这里是新检索、
#: 在那里是槽位答案——同一句话两个答案，正是 B1 那个 bug 的成因原型。
NEW_SEARCH_RE = re.compile(
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
#: **跨组比较算子**（I-030）：`(比较问句, 维度, 取最大?, 说法)`，按声明序求值。
#:
#: 它与 `_SUPERLATIVES` 是两张表、故意的：那张问「**这一组里**哪个最…」，
#: 这张问「**这几组各自那一项**里哪个更…」。比较级（「哪个更贵」）在单组里
#: 本来就该由最值算子回答，而在跨组里它才是主要说法。
#:
#: ⚠ **只在句子点名了 ≥2 组时求值**——这是本表误伤面不扩大的全部理由：
#: 它要求的条件比现状**更严**（要有两个组标签 + 每组各点到恰好一项），
#: 而不是又放宽一道口子。单组句子命中这张表也拿不到答案，照常进 Planner。
_COMPARATIVES: tuple[tuple[re.Pattern[str], str, bool, str], ...] = (
    (re.compile(r"哪(?:个|家|款|杯|份|一个|一款)?更?贵|哪(?:个|家|款|杯|份)?价(?:格|钱)高"),
     "price", True, "更贵"),
    (re.compile(r"哪(?:个|家|款|杯|份|一个|一款)?更?(?:便宜|划算|省钱)|"
                r"哪(?:个|家|款|杯|份)?价(?:格|钱)低"), "price", False, "更便宜"),
    (re.compile(r"哪(?:个|家|款|杯|份|一个)?(?:的)?评分(?:更)?高|哪(?:个|家)?分(?:更)?高|"
                r"哪(?:个|家)?口碑(?:更)?好"), "rating", True, "评分更高"),
    (re.compile(r"哪(?:个|家|一个)?(?:更)?近|哪(?:个|家)?距离(?:更)?近"),
     "distance", False, "更近"),
    (re.compile(r"哪(?:个|家|一个)?(?:更)?远|哪(?:个|家)?距离(?:更)?远"),
     "distance", True, "更远"),
    (re.compile(r"哪(?:个|家|一个)?关(?:门|店)?(?:更)?晚|哪(?:个|家)?(?:更)?晚关"),
     "closing", True, "关得更晚"),
    (re.compile(r"哪(?:个|家|一个)?关(?:门|店)?(?:更)?早|哪(?:个|家)?(?:更)?早关"),
     "closing", False, "关得更早"),
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

#: 第三种算子：**取第 N 项的某个维度**（`(维度问句, 维度)`，按声明序求值）。
#:
#: 它补的是前两种算子之间的缝。真栈实证（2026-08-19，Q10 残余批复跑）：菜单卡就在
#: 上一轮，「麦当劳的第七个多少钱」落到 chitchat，答**「第七个是脆汁鸡腿堡，10.90 元」**
#: ——第 7 项其实是柠檬脆脆麦旋风 16.00 元，**商品名和价格都是编的**。
#: 它同时躲开了两条既有守卫：最值/合计那条只认聚合算子，I-052 那条只在**零候选**
#: 时触发，而这里是**有候选的单项查询**。判据与两者同源：
#: **系统持有的事实绝不让 LLM 答。**
#:
#: ⚠ **序数与维度问句必须紧邻**，这是本条唯一的收窄手段，也是它与
#: 「行程内部的第 N 个」的分界：
#:   · 「麦当劳的第七个**多少钱**」→ 序数后直接是维度问句 ⇒ 劫持；
#:   · 「第二天第一个**景点**多少钱」→ 序数后是一个实质名词 ⇒ **不劫持**。
#: 这是 §9.27「算子+维度一起匹配，分开匹会误伤」那条纪律用在序数上的形态。
#: 分开匹（「句里有序数」+「句里有多少钱」）会把行程、菜谱、清单里的任何序数
#: 都吞掉，而这条短路误伤的代价是**整轮不进 Planner**。
_PICK_DIMS: tuple[tuple[str, str], ...] = (
    (r"多少钱|什么价|价钱|价格|几块钱|多少元|卖多少", "price"),
    (r"几点(?:关门|打烊|结束)|营业到几点|什么时候(?:关门|打烊)|关门时间", "closing"),
    (r"评分(?:是)?多少|几分|多少分|评分怎么样", "rating"),
    (r"多远|几公里|距离(?:是)?多少", "distance"),
)
#: 第四种算子：**重列**（C4-C，2026-08-28 QA 修复批第 2 批）。
#:
#: 它补的是「用户想再看一眼可选项」这个诉求：真栈 merchant T19/T20 连着两次说
#: 「重新列出刚才可以选择的项目」，前三种算子一条都不认（它们全要求「算子+维度」），
#: 于是整句进 Planner **重搜了一遍**——两次搜回**不同城市、不同门店**的列表，
#: 第二次 LLM 还凭空把检索地点定到了**青岛平度**。
#: 用户要的是「刚才那份」，系统手里就有那份，却给了他一份新的。
#:
#: ⚠ **不为重列扩白名单**：`_CANDIDATE_ITEM_KEYS` 那 13 键是与产生方的契约（§9.27），
#: 扩它要同步 `_PRODUCER_SHAPES`；而「再看一眼可选项」用**文字清单**（序号+名字+价格）
#: 已经闭环——按钮本来就还在刚才那张卡上，话术里说清楚就够了。
#:
#: 判据只有一段（算子词）+ 一道否决（不是新检索）：重列没有「维度」可配对，
#: 而这些说法本身已经在指向「刚才那份」——「重新列出」不可能是在说一次新检索。
_RELIST_RE = re.compile(
    r"重新列(?:出|表|一遍|一下)?|再列(?:一遍|一次|一下|出)|重列|"
    r"再(?:显示|念|报|说)(?:一遍|一次|一下)|"
    r"刚才(?:那)?(?:些|几)?(?:可以)?(?:选|选择|挑)?的?(?:选项|列表|清单|候选|项目)")

#: 序数与维度问句之间允许的虚词。**只许虚词**——放行任何实词就等于放弃紧邻判据。
_PICK_GLUE = r"(?:是|的|要|卖)?\s*"
_ORDINAL_PICKS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"第\s*({CN_NUM_SRC})\s*(?:个|家|项|条|种|款|杯|份)\s*"
                rf"{_PICK_GLUE}(?:{pattern})"), dim)
    for pattern, dim in _PICK_DIMS)


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


def _ordinal_pick_answer(text: str, items: list[dict]) -> str | None:
    """「第 N 个多少钱 / 几点关门 / 评分多少 / 多远」→ 确定性回答那一项的那个维度。

    三种「答不出来」全部**诚实说，不返回 None**——返回 None 就是把它交回 LLM 去编，
    而这条通道存在的全部理由正是「有候选却被编造了一个」。
    """
    for pattern, dim in _ORDINAL_PICKS:
        match = pattern.search(text)
        if not match:
            continue
        index = cn_int(match.group(1))
        if not index:
            return None
        if index > len(items):
            # ⚠ **不说「这份列表只有 N 项」**——那会说一句假话：候选集按
            # `_CANDIDATE_ITEM_KEYS` 裁到 10 项，而卡片上渲染的是 20 项，
            # 用户看得见第 15 项。诚实的说法是「我这边只跟到第 N 项」，
            # 说的是**系统记得多少**，不是**列表有多长**。
            return (f"刚才那份列表我这边只跟到第 {len(items)} 项，"
                    f"第 {index} 项够不着——你把名字说给我，我直接查。")
        item = items[index - 1]
        name = str(item.get("name") or "")
        value = dimension_value(item, dim)
        if value is None:
            return f"「{name}」这一项没带{_DIM_NOUN[dim]}，这个我算不出来。"
        return f"「{name}」{_render(dim, value)}。"
    return None


def _relist_answer(text: str, items: list[dict]) -> str | None:
    """「重新列出刚才的选项」→ 从候选台账重渲染**文字清单**，零 provider 调用。

    只念台账里真有的那几键（序号 + 名字 + 价格）。**没有的字段一个字都不补**
    ——重列的病本来就是「给了他一份新的」，再补一份想象出来的属性只是换个编法。

    话术末尾必须说清按钮在哪：候选集里没有 `send_text` 这类交互载体
    （白名单 13 键刻意不含它们），文字清单**回不到那张卡的按钮**，
    不说清就等于让用户以为按钮没了。
    """
    if not _RELIST_RE.search(text) or NEW_SEARCH_RE.search(text):
        return None
    lines = []
    for index, item in enumerate(items, start=1):
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        price = dimension_value(item, "price")
        lines.append(f"{index}. {name}"
                     + (f"（{_render('price', price)}）" if price is not None else ""))
    if not lines:
        return None
    return ("刚才那份列表我这边跟到这几项：" + "；".join(lines)
            + "。想选哪个就说「第几个」——按钮还在刚才那张卡上。")


def _group_slices(text: str, groups: list[dict]) -> list[tuple[dict, str]]:
    """按各组标签出现的位置把原话切成「这一段在说这一组」（I-030）。

    「麦当劳的第二个和瑞幸的第二个哪个贵」→
    `[(mcd 组, "麦当劳的第二个和"), (luckin 组, "瑞幸的第二个哪个贵")]`。
    序数**归属最近的前一个标签**——不切段而在整句里找序数，就会把两个「第二个」
    都塞给同一组，那正是这条通道要修的错。

    ⚠ **同位置只留最新那一组**：「附近的麦当劳」→「看看菜单」会产生两个都叫
    「麦当劳」的组，它们指的是同一家的两份东西。留两份会切出一个空段，
    读起来像「跨组」其实不是。
    """
    at_group: dict[int, dict] = {}
    for entry in groups:
        at = label_hit(text, entry)
        if at is not None:
            at_group[at] = entry            # 后来的（更新的）覆盖同位置的旧组
    marks = sorted(at_group.items())
    return [(entry, text[at:(marks[i + 1][0] if i + 1 < len(marks) else len(text))])
            for i, (at, entry) in enumerate(marks)]


def _cross_group_picks(
        text: str, groups: list[dict]) -> tuple[list[tuple[str, dict]], str | None]:
    """每个被点名的组各自点到的那**一项** → `([(组标签, 候选项), ...], 越界话术)`。

    **任一组点不到恰好一项就整句放弃**（返回空）。跨组的错比单组贵：它会把
    两家的东西比成一家的，而话术里两个名字都在、看起来毫无异常
    ——同 `candidate_ref` 那条「命中多项一律不动」。

    ⚠ **越界与「点不到」是两件事，出口也不同**（同 `_ordinal_pick_answer` 那条）：
    「第十五个」是一个**明确的**引用，只是我们跟不到那么远 ⇒ 诚实说系统记得多少；
    「麦当劳和瑞幸的第二个」是我们分不清他在说哪一项 ⇒ 不劫持，交回 Planner。
    把前者也返回 None，就是把一个明确的问题交回 LLM 去编。
    """
    out: list[tuple[str, dict]] = []
    for entry, segment in _group_slices(text, groups):
        items = [it for it in (entry.get("items") or []) if isinstance(it, dict)]
        ordinals = _ordinals(segment)
        if len(ordinals) == 1 and ordinals[0] > len(items):
            label = str(entry.get("label") or "")
            return [], (f"{label}那份我这边只跟到第 {len(items)} 项，"
                        f"第 {ordinals[0]} 项够不着——你把名字说给我，我直接查。")
        hit = [items[i - 1] for i in ordinals if 0 < i <= len(items)]
        if not hit:
            hit = _named(segment, items)
        if len(hit) != 1:
            return [], None
        out.append((str(entry.get("label") or ""), hit[0]))
    return out, None


def _cross_group_answer(text: str, groups: list[dict]) -> str | None:
    """跨组比较 / 跨组合计（I-030）→ 确定性话术，或 None（不劫持）。

    **算子闸排在解析之前**是判据不是顺序：先确认这句话确实在问「哪个更…／一共」，
    才谈得上把它拆成两组引用。反过来做会让「麦当劳的第十五个和瑞幸的第二个」
    这种**没有算子**的句子也被越界话术接管——那是把误伤面往回放宽。

    话术把**两边的数都念出来**再给结论：跨组结论只有一个词（「更贵」），
    用户没法核对它是不是拿对了组——而拿错组恰恰是这条通道要修的病。
    """
    compare = next(((dim, want_max, phrase)
                    for pattern, dim, want_max, phrase in _COMPARATIVES
                    if pattern.search(text)), None)
    total = bool(_TOTAL_RE.search(text) and _TOTAL_DIM_RE.search(text))
    if compare is None and not total:
        return None
    picks, overflow = _cross_group_picks(text, groups)
    if overflow:
        return overflow
    if len(picks) < 2:
        return None
    return (_compare_answer(picks, *compare) if compare
            else _cross_total_answer(picks))


def _cross_render(picks: list[tuple[str, dict]], dim: str) -> str:
    return "、".join(
        f"{label}的「{it.get('name')}」{_render(dim, dimension_value(it, dim))}"
        for label, it in picks)


def _missing_answer(picks: list[tuple[str, dict]], dim: str, verb: str) -> str:
    missing = [f"{label}的「{it.get('name')}」" for label, it in picks
               if dimension_value(it, dim) is None]
    return f"{'、'.join(missing)}没带{_DIM_NOUN[dim]}，这个我{verb}不了。"


def _compare_answer(picks, dim: str, want_max: bool, phrase: str) -> str:
    if any(dimension_value(it, dim) is None for _, it in picks):
        return _missing_answer(picks, dim, "比")
    values = [dimension_value(it, dim) for _, it in picks]
    body = _cross_render(picks, dim)
    if len(set(values)) == 1:
        # 相等时说「一样」，**不点名**——把两个名字都塞进「更贵」读起来像是
        # 系统选出了赢家，那是用一句确定的话说错一件事。
        return f"{body}——两边{_DIM_NOUN[dim]}一样。"
    best = max(values) if want_max else min(values)
    head = "、".join(f"「{it.get('name')}」" for (_, it), v in zip(picks, values)
                    if v == best)
    return f"{body}——{head}{phrase}。"


def _cross_total_answer(picks) -> str:
    """跨组合计。用 ` + ` 而不是顿号连接，与单组 `_total_answer` 同款：
    **算式要能被用户当场核对**，这是确定性回答该有的样子。"""
    if any(dimension_value(it, "price") is None for _, it in picks):
        return _missing_answer(picks, "price", "算")
    priced = [(label, str(it.get("name") or ""), dimension_value(it, "price"))
              for label, it in picks]
    parts = " + ".join(f"{label}的「{name}」{v:.2f}" for label, name, v in priced)
    return f"{parts}，一共 {sum(v for _, _, v in priced):.2f} 元。"


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
    if not t or NEW_SEARCH_RE.search(t):
        return False
    if not _has_reference(t, [it for it in (items or []) if isinstance(it, dict)]):
        return False
    if any(pattern.search(t) for pattern, _, _ in _SUPERLATIVES):
        return True
    if any(pattern.search(t) for pattern, _ in _ORDINAL_PICKS):
        return True
    return bool(_TOTAL_RE.search(t) and _TOTAL_DIM_RE.search(t))


def answer(text: str, entry: dict | None,
           named: list[dict] | None = None) -> str | None:
    """→ 确定性话术，或 None（=不劫持，照常进 Planner）。

    `entry` / `named` 都来自 `context.resolve_candidate_scope(text, focus)`
    ——**候选集该绑哪一组的口径只有一份**，这里不发明第二套。
    `entry` 是主组（零命中时就是 `newest_candidate_set`，行为逐字同旧），
    `named` 是句子**点名了的**那几组，只有它 ≥2 时才谈得上跨组（I-030）。

    跨组排在单组**之前**：一句话点名了两组，就不该由其中一组独自回答
    ——那正是 I-030 那个「答出另一家的真商品真价格」的形态。
    """
    groups = [g for g in (named or []) if isinstance(g, dict)]
    if len(groups) >= 2 and not NEW_SEARCH_RE.search(str(text or "")):
        got = _cross_group_answer(str(text).strip(), groups)
        if got:
            return got
    items = [it for it in ((entry or {}).get("items") or []) if isinstance(it, dict)]
    if not items:
        # 零候选那一支由 I-052 那条守卫兜（它管的是「引用了却没有」），
        # 这里只是不劫持。
        return None
    t = str(text).strip()
    # 重列排在聚合判据**之前**：它自带引用（「刚才那份」），
    # 而 `is_candidate_aggregate_question` 要的是「引用 + 算子 + 维度」三段，
    # 重列一段都凑不齐——放在后面等于永远走不到。
    got = _relist_answer(t, items)
    if got:
        return got
    if not is_candidate_aggregate_question(text, items):
        return None
    if len(items) >= 2:
        # 最值与合计**要求至少两项**——一项时没有「哪家最…」「一共」可言。
        # 序数取值没有这条限制：只读菜单命中单品后候选集就只有一项，
        # 而「第一个多少钱」在那时照样是个有答案的问题。
        got = _total_answer(t, items) or _superlative_answer(t, items)
        if got:
            return got
    return _ordinal_pick_answer(t, items)
