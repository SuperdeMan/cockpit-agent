"""执行事实的审计出口——**确定性、零 LLM**（Q6，2026-08-16）。

「刚才实际执行了什么」是系统持有的事实。此前它没有可查询的事实源
（`task_ledger` 只收 research/mcp_order，车控/导航/提醒/场景一条都不进），
chitchat 只能拿对话历史让 LLM 重构，真栈三次取样读出三个样：

| 取样 | 回答 | 真实动作 |
|---|---|---|
| ① | 「打开了车窗，音乐暂停了」 | ✅ |
| ② | 「**关了车窗**，停了音乐」 | ❌ 方向说反 |
| ③ | 「车窗没动，音乐也没停——我这边只是文字回复，**没法真的控制车**」 | ❌ 否认执行过 |

`window.open` + `media.pause` 逐字在案。

> 判据：**「系统持有的事实绝不让 LLM 答」**（墙钟三件套的既有纪律）。
> 这里不是「让 LLM 答得更好」——加提示词治不了它，因为模型手里根本没有那些数。
> 是**根本不问 LLM**：答案由会话历史里的 `actions` 直接拼出来，同一份历史逐字同一个答案。

## 两条设计判据

1. **判据必须窄。** chitchat 是兜底，看到的是**全部流量**——阶段 1 那次 `"灯亮"`
   通配就是在接上 chitchat 之后才暴露误杀面的。所以要求**回顾指代**与**执行询问**
   两类词同时命中：「刚才那家店叫什么」只有前者、「帮我执行一下」只有后者，都不劫持。
2. **报给用户的是原话不是命令名。** 两者都来自系统持有的记录、都不经 LLM；
   但 `window.open` 对用户没有意义，而「把车窗打开」天然可核对——用户记得自己说过什么。
"""
from __future__ import annotations

import re

#: 回顾指代：这句话问的是**过去发生的事**。
_RETROSPECT_RE = re.compile(r"刚才|刚刚|方才|这次对话|本次对话|本次会话|这次会话|刚")
#: 执行询问：问的是**做了什么**，不是问能做什么、也不是问某个具体对象。
_EXECUTION_RE = re.compile(
    r"(执行|操作|做|干)了?(什么|哪些|啥)|做过(什么|哪些)|"
    r"(执行|操作)过(什么|哪些|哪几)|执行了哪|干了(什么|啥)")
#: 这些即使两类都命中也不是审计问题——它们各有自己的卡。
_NOT_AUDIT_RE = re.compile(r"订单|账单|提醒|日程")


def is_execution_audit_question(raw_text: str) -> bool:
    """这句话是不是在问「刚才实际执行了什么」。**确定性纯函数。**"""
    text = str(raw_text or "")
    if not text or _NOT_AUDIT_RE.search(text):
        return False
    return bool(_RETROSPECT_RE.search(text) and _EXECUTION_RE.search(text))


def _clean_turn(turn) -> tuple[str, str, list[str], str]:
    """历史来自 gRPC，形状不可信——非 dict / text 非 str / actions 非 list 都要吃得下。

    同 CLAUDE.md §6：**防御要一路防到真正会被拿去用的那个值**，
    不是防到最外层容器为止。
    """
    if not isinstance(turn, dict):
        return "", "", [], ""
    role = turn.get("role") if isinstance(turn.get("role"), str) else ""
    text = turn.get("text") if isinstance(turn.get("text"), str) else ""
    exch = turn.get("exchange_id") if isinstance(turn.get("exchange_id"), str) else ""
    raw = turn.get("actions")
    acts = [a for a in raw if isinstance(a, str) and a.strip()] \
        if isinstance(raw, (list, tuple)) else []
    return role, text, acts, exch


def executed_exchanges(history) -> list[tuple[str, list[str]]]:
    """从会话历史抽出 `(用户原话, 该轮执行的动作名)`，按发生顺序。

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
    for role, text, _acts, exch in turns:
        if role == "user" and exch and exch not in said:
            said[exch] = text
    out: list[tuple[str, list[str]]] = []
    pending_user = ""            # 仅供无 exchange_id 的存量轮次回退
    for role, text, acts, exch in turns:
        if role == "user":
            pending_user = text
        if acts:
            out.append((said.get(exch, pending_user if not exch else ""), acts))
    return out


def audit_answer(history) -> str:
    """审计问题的确定性回答。**同一份历史 → 逐字同一个答案。**"""
    done = executed_exchanges(history)
    if not done:
        return "这次对话里我还没有执行过任何操作。"
    said = [u.strip() for u, _acts in done if u.strip()]
    n = sum(len(acts) for _u, acts in done)
    if not said:
        # 有动作但取不到原话（存量轮次/异常形状）——**报数仍然是真的**，
        # 别因为凑不齐人话就退回「没有执行过」，那是把事实说反。
        return f"这次对话里执行过 {n} 个操作，但我没能取到对应的原话。"
    return f"这次对话里执行过 {n} 个操作：" + "、".join(said) + "。"
