"""「系统持有的会话事实」的判据与话术——**唯一实现**（C4，2026-08-28）。

## 这一族问题问的都是系统自己手里的东西

2026-08-26 MiniMax 长会话 QA 一口气交来五张症状卡，它们看着像五件事：

| 轮 | 用户问的 | 系统答的 | 事实其实在哪 |
|---|---|---|---|
| info T41 | 这个行情的数据源是什么 | 「东方财富实时行情、19:23 前后」 | 卡上的 `_prov`（真实是 Tushare / 20260826） |
| info T43 | （追问）| 编造一段「把上证指数当成沪深300」的自我纠错 | 同上 |
| info T55 | 总结这五轮里执行了什么 | 「手册里没有查到」 | `AppendTurn.actions` 账本 |
| vehicle T51 | 现在还有待确认的操作吗 | 「嗯」 | 挂起表 |
| info T56 | 同一句 | 答了学校地址 | 同上 |

**没有一条是模型不够聪明**：这些答案都在系统手里，缺的是**一条确定性通道**。
落到 chitchat 之后它只有 4 轮纯文本历史（actions / 卡片 / `_prov` 都不进 prompt），
**它连想如实回答都没有材料，编造是结构性的**。

> 判据：**凡是「系统持有的事实」，判据面就得是闭合的**（Q10 第 7.5 步那条的第四次
> 扩面）。以及 Q6 的老账：**话术层判据验证不了「说的是不是真的」**——所以读出口
> 只念账本，零 LLM、零网络。

## 为什么住在 runtime/

因为它有**两个够不着彼此的消费方**：编排层的确定性短路（`orchestrator/cloud`）与
chitchat 的兜底闸（`agents/chitchat`）。云侧编排镜像不 `COPY agents/`，
agent 镜像不 `COPY orchestrator/`，而**两边都 `COPY runtime`**。
在两边各写一份判据，正是 B1 那个 bug 的成因（判定抄两份）。

Q6 把审计闸建在 chitchat 里，代价 2026-08-26 兑现了：T37「刚才实际改了哪一条」
被 planner 接给了 reminder.list，**闸在 chitchat 里，别的域接走就够不着**。
所以判据搬家到这里、挂点搬到落域**之前**——两件事都要做，只搬一件都不够。

## 三条读出口共用的纪律

1. **有账才答、无账明说。** 账本里没有的东西一个字都不许补，宁可说「我这边没记到」。
2. **判据要窄。** 编排层的短路看到的是**全部流量**，误伤代价是「整轮不进 Planner」
   ——比 chitchat 兜底那条高一档。所以每条出口都要求两段以上同时命中。
3. **同一份账 → 逐字同一个答案。** 确定性的直接证据是零方差。
"""
from __future__ import annotations

import re

from runtime.clock import local_dt
from runtime.question_shape import DIRECTIVE_MARKERS

# ── ① 执行史：「刚才实际执行了什么」 ──────────────────────────────────────
#
# 原实现 2026-08-16 落在 `agents/chitchat/src/audit.py`（Q6），2026-08-28 迁入。
# 迁入前那份 docstring 记着它存在的理由，逐字保留在这里：
#
#   「刚才实际执行了什么」是系统持有的事实。此前它没有可查询的事实源
#   （`task_ledger` 只收 research/mcp_order，车控/导航/提醒/场景一条都不进），
#   chitchat 只能拿对话历史让 LLM 重构，真栈三次取样读出三个样：
#     ① 「打开了车窗，音乐暂停了」 ✅
#     ② 「**关了车窗**，停了音乐」 ❌ 方向说反
#     ③ 「车窗没动，音乐也没停——我这边只是文字回复，**没法真的控制车**」 ❌ 否认执行过
#   `window.open` + `media.pause` 逐字在案。
#
#: 回顾指代：这句话问的是**过去发生的事**。
#: 「这N轮/这几轮」是 C4 补的——T55 原话是「总结这五轮里哪些执行了」，
#: 而原词表只有「刚才/这次对话」，于是一句标准的审计问题一个词都不命中。
_RETROSPECT_RE = re.compile(
    r"刚才|刚刚|方才|这次对话|本次对话|本次会话|这次会话|刚|"
    r"这\s*(?:几|[一二三四五六七八九十两]|\d+)\s*(?:轮|次|条|句|个回合)|"
    r"这一?(?:路|通)|到目前为止|截至目前")
#: 执行询问：问的是**做了什么**，不是问能做什么、也不是问某个具体对象。
#:
#: 三条分支，后两条是 C4 补的：
#:   · 泛问——`(执行|操作|做|干)了什么/哪些/啥`（Q6 原样）；
#:   · **指名问**——`(执行|操作|改|修改|调整|设置|设|动)了哪(一|几)?(条|个|项|些|步)`。
#:     T37 原话「刚才实际改了哪一条，时间是什么」在第一条分支上一个词都不命中，
#:     于是被 planner 接给 reminder.list、**答了整份列表**。
#:   · **倒装问**——`哪些/哪几条 + (被)? + 执行了`。T55 原话是「总结这五轮里
#:     **哪些执行了**」：疑问词在动词**前面**，而前两条分支都写成了「动词在前」。
#:     ⚠ 这一条是写测试时才发现的：我照着 T37 补完指名问，用 T55 原话一跑就红
#:     ——**同一个语义有两种语序，词表只写了一种**。
#: 后两条刻意比第一条严一格（必须有「了」+「哪」+量词，或疑问词与动词**紧邻**），
#: 因为它们引进了 `改/设置` 这些高频动词：放宽到「改什么」会把「帮我改个名字」
#: 也吞掉，放宽到「哪些…做了」会把「哪些菜做了改动」吞掉。
_EXECUTION_RE = re.compile(
    r"(执行|操作|做|干)了?(什么|哪些|啥)|做过(什么|哪些)|"
    r"(执行|操作)过(什么|哪些|哪几)|执行了哪|干了(什么|啥)|"
    r"(执行|操作|改|修改|调整|设置|设|动)了哪(?:一|几)?(?:条|个|项|些|步)|"
    r"哪(?:些|几[条个项])(?:被)?(?:执行|操作|做|改|修改|调整|设置)了")
#: 这些即使两类都命中也不是审计问题——它们各有自己的卡。
#: ⚠ `做什么的` 是 C4 补的**误伤对照**：「刚才那家店是做什么的」在原词表上
#: 两段全中（刚才 + 做什么），在 chitchat 兜底位上代价还小，搬到编排层就是
#: **整轮不进 Planner**。判据搬家会改变误伤代价，词表必须跟着重看一遍。
_NOT_AUDIT_RE = re.compile(r"订单|账单|提醒|日程|(?:做|干)(?:什么|啥)的")


def refers_to_an_earlier_turn(raw_text: str) -> bool:
    """这句话在**回指前面某一轮**（「刚才/刚刚/这几轮/上面那次」）。

    与执行史闸共用同一张回顾词表，**刻意暴露出来给第二个消费方**：
    engine 的股票焦点继承要判同一件事（「只总结**刚才查到的**行情」）。
    两处各写一份词表，就会出现「审计闸认得的说法焦点继承不认得」这种
    只有真栈才发现得了的分歧——B1 那条在词表层的形态。
    """
    return bool(_RETROSPECT_RE.search(str(raw_text or "")))


def is_execution_audit_question(raw_text: str) -> bool:
    """这句话是不是在问「刚才实际执行了什么」。**确定性纯函数。**"""
    text = str(raw_text or "")
    if not text or _NOT_AUDIT_RE.search(text):
        return False
    return bool(_RETROSPECT_RE.search(text) and _EXECUTION_RE.search(text))


def _clean_turn(turn) -> tuple[str, str, list[str], str, int]:
    """历史来自 gRPC，形状不可信——非 dict / text 非 str / actions 非 list 都要吃得下。

    同 CLAUDE.md §6：**防御要一路防到真正会被拿去用的那个值**，
    不是防到最外层容器为止。
    """
    if not isinstance(turn, dict):
        return "", "", [], "", 0
    role = turn.get("role") if isinstance(turn.get("role"), str) else ""
    text = turn.get("text") if isinstance(turn.get("text"), str) else ""
    exch = turn.get("exchange_id") if isinstance(turn.get("exchange_id"), str) else ""
    raw = turn.get("actions")
    acts = [a for a in raw if isinstance(a, str) and a.strip()] \
        if isinstance(raw, (list, tuple)) else []
    ts = turn.get("ts")
    ts = int(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else 0
    return role, text, acts, exch, ts


def executed_exchanges(history) -> list[tuple[str, list[str], int]]:
    """从会话历史抽出 `(用户原话, 该轮执行的动作名, 发生时刻)`，按发生顺序。

    动作挂在 **assistant** 轮上（它才是执行发生之后写的那一条），
    而要报给用户的是**同一 exchange 的 user 原话**。

    ⚠ **必须按 `exchange_id` 绑，不能按位置猜**（2026-08-16 真栈当场抓到）。
    端侧 `_record_local_turn` 是 fire-and-forget，两轮快指令的真实落库顺序是：

        user 打开车窗(A) → user 暂停音乐(B) → assistant 好的(A) → assistant 好的(B)

    「往前找最近一条 user 轮」于是两次都拿到「暂停音乐」，答出
    **「执行过 2 个操作：暂停音乐、暂停音乐」**——张冠李戴，比不回答更糟。
    而 `exchange_id` 本来就是为这件事存在的：M-B 的契约逐字写着它把一轮 user 请求
    与其可见 assistant 回复「绑成一个**不可拆的账目单元**」。
    > **判据**：写位置启发式之前先问一句「有没有一个字段就是干这个的」。
    > 我的单测没抓到它，因为测试历史是**理想顺序**——探针替被测系统提供了前提。

    存量轮次没有 `exchange_id`（本字段之前写的）：**只有这种轮次才回退位置启发式**，
    并且回退只在同一份历史里没有任何 exchange 信息时才发生。
    """
    turns = [_clean_turn(t) for t in (history or [])]
    said = {}
    for role, text, _acts, exch, _ts in turns:
        if role == "user" and exch and exch not in said:
            said[exch] = text
    out: list[tuple[str, list[str], int]] = []
    pending_user = ""            # 仅供无 exchange_id 的存量轮次回退
    for role, text, acts, exch, ts in turns:
        if role == "user":
            pending_user = text
        if acts:
            out.append((said.get(exch, pending_user if not exch else ""), acts, ts))
    return out


#: 「顺便把时间也说一下」。T37 原话是「刚才实际改了哪一条，**时间是什么**」——
#: 只报动作等于只答了半句。缺省不带时刻是刻意的：那是 Q6 上线以来的行为锁，
#: 改它要有人问才对（同「未声明的产生方逐字零行为变化」）。
_WHEN_ASK_RE = re.compile(r"时间(?:是)?(?:什么|多少|几点)|什么时候|几点|"
                          r"时刻|哪个时间|when")


def asks_when(raw_text: str) -> bool:
    """这句话在追问「什么时候」。"""
    return bool(_WHEN_ASK_RE.search(str(raw_text or "")))


def audit_answer(history, *, with_time: bool = False) -> str:
    """审计问题的确定性回答。**同一份历史 → 逐字同一个答案。**

    `with_time` 由调用方按原话判（`asks_when`）：账本里本来就有 `ts`，
    但不问不报——多说一个维度也是改行为。时刻一律走 `runtime.clock`
    的业务时区，**不用 `time.localtime`**（容器 TZ=UTC，那条老账已经犯过四次）。
    """
    done = executed_exchanges(history)
    if not done:
        return "这次对话里我还没有执行过任何操作。"
    n = sum(len(acts) for _u, acts, _ts in done)
    said = [(u.strip(), ts) for u, _acts, ts in done if u.strip()]
    if not said:
        # 有动作但取不到原话（存量轮次/异常形状）——**报数仍然是真的**，
        # 别因为凑不齐人话就退回「没有执行过」，那是把事实说反。
        return f"这次对话里执行过 {n} 个操作，但我没能取到对应的原话。"
    if with_time:
        parts = [f"{u}（{local_dt(ts).strftime('%H:%M')}）" if ts else u
                 for u, ts in said]
    else:
        parts = [u for u, _ts in said]
    return f"这次对话里执行过 {n} 个操作：" + "、".join(parts) + "。"


# ── ② 数据源：「这个数据是哪来的、什么时候的」 ────────────────────────────
#
#: 数据源问句的词表。**从 `agents/info/src/handlers/stock.py` 下沉**（原
#: `_PROVENANCE_MARKERS`，2026-07 起就在那里守着 info.stock 的直答分支）。
#: 下沉的理由与判据搬家同一条：stock 内部那份只有在**路由到 info.stock 之后**
#: 才够得着，而 T41 的病恰恰是**没路由到**（stock 能力零 route_hint、零范例，
#: MiniMax 把它落到了 chitchat）。护栏全部建在「被路由到之后」，等于没有护栏。
#:
#: ⚠ 刻意**不含裸「来源」**：stock 那份的注释写着「『来源』本身可能在问公司收入/
#: 业务来源」，这条约束在编排层只会更重要（这里看到的是全部流量）。
PROVENANCE_MARKERS = (
    "数据源", "数据来源", "行情来源", "报价来源", "股价来源", "价格来源",
    "更新时间", "行情时间", "报价时间", "更新到", "什么时候更新",
)


def is_provenance_question(raw_text: str) -> bool:
    """这句话是不是在问「这个数据是谁给的 / 什么时候的」。

    两段：命中来源词表 ∧ **不是祈使式**。第二段复用
    `runtime.question_shape.DIRECTIVE_MARKERS`——「帮我把更新时间改一下」是个指令，
    不是提问，那张表本来就是为这件事存在的，这里不发明第二份。
    """
    text = str(raw_text or "")
    if not text or any(w in text for w in DIRECTIVE_MARKERS):
        return False
    return any(mark in text for mark in PROVENANCE_MARKERS)


#: 真实性模式的人话（契约 §9.3 的取值域）。**不认识的模式原样报**——
#: 编一个中文名出来只会让「模式是什么」这件事变得不可核对。
_MODE_LABEL = {"real": "实时真实数据源", "mock": "模拟数据",
               "degraded": "降级数据源", "cached": "缓存数据",
               "deterministic": "系统内确定性结果"}


def latest_sources(history) -> list[dict]:
    """账本里**最近一轮有来源记录的那一轮**的全部来源。没有 → 空列表。

    取最近一轮而不是全会话汇总：用户问「这个数据哪来的」时说的是上一张卡。
    汇总会把三轮前那家 provider 一起念出来，**每个字都是真的、合起来是错的**
    ——同 I-030「答错组比编造更难被发现」。
    """
    for turn in reversed(list(history or [])):
        if not isinstance(turn, dict):
            continue
        got = [s for s in (turn.get("sources") or []) if isinstance(s, dict)
               and str(s.get("vendor") or "").strip()]
        if got:
            return got
    return []


def provenance_answer(history) -> str:
    """数据源问题的确定性回答。**只念账本；账本空就说空。**

    「数据自己是什么时候的」由产生方声明称呼（`data_time_label`，缺省「数据时间」）
    ——行情卡那一维叫「行情时间」，而这段代码**不该认识这个词**。同 `_candidate_label`：
    编排层看不出 `mcd.menu` 那组该叫「麦当劳」，也看不出 `stock_quote` 那个时刻该怎么称呼。

    取数时刻与数据时刻**两个都念**：真栈 T41 编出的「19:23 前后」正是把取数时刻当成了
    行情时刻，只念一个就等于让用户没法发现这种混淆。
    """
    got = latest_sources(history)
    if not got:
        return ("这次对话里我还没有调用过外部数据源，所以没有来源可以报。"
                "您要是问的是某一张卡，把它的内容说一句给我，我重新查一次。")
    parts = []
    for src in got:
        vendor = str(src.get("vendor") or "").strip()
        mode = str(src.get("mode") or "").strip()
        label = _MODE_LABEL.get(mode, mode)
        piece = f"{vendor}（{label}）" if label else vendor
        data_time = str(src.get("data_time") or "").strip()
        if data_time:
            time_label = str(src.get("data_time_label") or "").strip() or "数据时间"
            piece += f"，{time_label} {data_time}"
        when = str(src.get("fetched_at") or "").strip()
        if when:
            piece += f"，取数时间 {when}"
        note = str(src.get("note") or "").strip()
        if note:
            piece += f"，{note}"
        parts.append(piece)
    return "上一条外部数据的数据来源是 " + "；".join(parts) + "。"


# ── ③ 挂起状态：「现在还有待确认的操作吗」 ────────────────────────────────
#
#: 挂起问句词表。`system.no_pending` 此前只在**裸确认词/确认帧**上触发
#: （`engine.py` 的 `_is_bare_confirm_word` 那一支），**问句形态没有出口**
#: ⇒ 落到 chitchat，真栈答了一个字「嗯」（vehicle T51），另一个 persona 答了
#: 学校地址（info T56，旧 pickup 焦点顶着）。
_PENDING_ASK_RE = re.compile(
    r"待确认|待办的操作|没(?:有)?确认|未确认|还没确认|等(?:着|我)?确认|"
    r"需要(?:我)?确认|要(?:我)?确认|确认(?:的|过的)?(?:操作|事项|东西)")
#: 问形闸。与词表是 AND：「确认第二个」「取消待确认的」都是**指令**，不是提问。
_PENDING_QUESTION_TAIL = ("吗", "吗？", "吗?", "呢", "呢？", "呢?", "?", "？")
_PENDING_QUERY_RE = re.compile(r"还有|有没有|有几|多少|哪些|是什么|剩(?:下|余)")


def is_pending_question(raw_text: str) -> bool:
    """这句话是不是在问「还有没有等我确认的操作」。

    三段：挂起词表 ∧ 疑问形态（尾词或查询词）∧ 非祈使。第三段同 ②：
    「帮我确认待确认的操作」是指令，短路吞掉它就是把一次执行请求变成一句播报。
    """
    text = str(raw_text or "").strip()
    if not text or any(w in text for w in DIRECTIVE_MARKERS):
        return False
    if not _PENDING_ASK_RE.search(text):
        return False
    return bool(text.rstrip("。！!.~ ").endswith(_PENDING_QUESTION_TAIL)
                or _PENDING_QUERY_RE.search(text))


def pending_answer(pendings) -> str:
    """挂起问题的确定性回答。`pendings` = `[{"what": 目标描述, "phase": 阶段}, ...]`。

    **报的是挂起表里真有的那几条**，一条不多一条不少；空表就说没有——
    同 §9.x 那条「无账明说」。目标描述由调用方从挂起态里取（编排层才有那个结构），
    这里只负责把它念成人话：**判据与话术在一起，取数在调用方**。
    """
    live = [p for p in (pendings or []) if isinstance(p, dict)]
    if not live:
        return "当前没有待确认的操作。"
    parts = []
    for item in live:
        what = str(item.get("what") or "").strip() or "刚才那个操作"
        ask = "确认" if str(item.get("phase") or "") == "wait_confirm" else "补充信息"
        parts.append(f"「{what}」等你{ask}")
    head = f"有 {len(parts)} 条待确认的操作：" if len(parts) > 1 else "有 1 条待确认的操作："
    return head + "、".join(parts) + "。说「确认」就执行，说「取消」就作废。"
