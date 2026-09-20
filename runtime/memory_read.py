"""记忆 / 会话历史**读取的三态**与「这句话在问记忆吗」——唯一实现（评审 W17，批 5，2026-09-20）。

## 它解决什么

此前一次读取只有两种结果：读到了东西，或者一个空列表。而空列表背后是**两件事**：

- 「读到了，是空的」——用户从没说过；
- 「根本没读到」——memory 服务连不上、PG 掉了退到进程内存、Redis 掉了退到内存……

云侧 `ContextManager._recall` / `_history` 各自 `except Exception: return []`，memory 服务在
后端掉线时也静默退到内存兜底并返回空——四层各吞一次之后，一次 PG 故障会让
「你还记得我不吃辣吗」得到一句**自信的**「你没说过」，「刚才执行了什么」得到「没有执行记录」。
这与 `_pending_digest` 那条老账同形：**两者都返回 `[]` 就会让一次故障说出一句听起来很确定的假话。**

## 三态

| 态 | 含义 | 谁来判 |
|---|---|---|
| `found` | 读到了 ≥1 条 | 读侧 |
| `none` | 读到了、是空的 | 读侧 |
| `unavailable` | 读没成：RPC 失败，或服务自报 `degraded`（配置了持久后端却在用内存兜底） | 读侧 |

`unavailable` 时消费方**不得**把空当成事实：确定性读出口说「这会儿查不到」，规划照常（记忆缺席
只是少了个性化）。找到 / 没有两态行为逐字同旧。

## 为什么住在 runtime/

判据有两个够不着彼此的消费方（云侧编排的确定性出口、chitchat 自己的召回），两边都 `COPY runtime`
——同 `session_facts` 的落点理由。话术也放这里：同一件事在两处出现时必须是同一句。
"""
from __future__ import annotations

import re

from runtime.memory_directive import POLITE_PREFIX_RE

FOUND = "found"
NONE = "none"
UNAVAILABLE = "unavailable"
#: 没有去读（用户关了记忆 / 没有 user_id）——不是故障也不是空，观测列要分得开。
OFF = "off"
READ_STATES = frozenset({FOUND, NONE, UNAVAILABLE, OFF})


def read_state(items, *, degraded: bool = False, failed: bool = False) -> str:
    """一次读取的结局。`failed`（RPC 没回来）⇒ `unavailable`；`degraded`（服务自报配置了持久
    后端却在用内存兜底）时**空**才是 `unavailable`——兜底内存里的空是故障的空，不是用户的空；
    兜底里读到了东西说明是本进程写进去的，那部分是真的，照报 `found`。"""
    if failed:
        return UNAVAILABLE
    if items:
        return FOUND
    return UNAVAILABLE if degraded else NONE


# ── 「这句话在问记忆吗」 ─────────────────────────────────────────────────────
#
# 问句形态 + 记忆动词 / 「我以前说过」框架，零领域词。排除「记住…」祈使（那是写不是读，
# `memory_directive` 唯一实现）与「我不记得了」这类自述。判据窄：编排层的短路看到的是全部
# 流量，误伤代价是整轮不进 Planner（`session_facts` 那条纪律）。
_MEMORY_VERB = r"(?:还)?(?:记得|记不记得|记不得|记住了|有没有记住|有印象|有没有印象)"
_MEMORY_RECALL_RE = re.compile(
    # 「你还记得…吗」「你记不记得我…」「记得我上次说的吗」
    rf"^(?:你|您)?(?:那)?{_MEMORY_VERB}"
    # 「我之前/上次/以前/昨天跟你说过…吗」「我有没有跟你说过…」
    rf"|^(?:我|咱|咱们)(?:之前|以前|上次|上回|昨天|前几天|上周|刚才|先前|早前)?(?:有没有|是不是)?"
    rf"(?:跟你|和你|对你|给你)?(?:说过|提过|讲过|告诉过|说了)"
    # 「你知道我喜欢/爱吃/常去…吗」「你知不知道我…」
    rf"|^(?:你|您)(?:知道|知不知道|了解|了不了解|清楚|清不清楚)(?:我|咱)")
#: 「请问 / 问一下」这类提问前缀（`POLITE_PREFIX_RE` 只剥「请」）。
_ASK_PREFIX_RE = re.compile(r"^(?:请问|问一下|问下|想问|想问一下)[，,]?\s*")
_QUESTION_MARKS = ("吗", "么", "嘛", "呢", "?", "？", "记不记得", "有没有", "是不是",
                   "知不知道", "了不了解", "清不清楚")
#: 自述而非提问：「我不记得了」「我记得是八点」——句首是「我（不）记得」且没有对助手的指向。
_SELF_STATEMENT_RE = re.compile(r"^(?:我|咱)(?:不|没|也不)?(?:记得|记住|有印象)")


def is_memory_recall_question(text: str | None) -> bool:
    """「你还记得我不吃辣吗」「我之前说过我老婆爱吃什么吗」「你知道我常去哪吗」→ True。
    「记住我喜欢清淡」（祈使）/「我不记得了」（自述）/「我喜欢清淡」（陈述）→ False。"""
    # 先剥「请问」再剥「请」：`POLITE_PREFIX_RE` 会把「请问」啃成「问…」
    raw = POLITE_PREFIX_RE.sub("", _ASK_PREFIX_RE.sub("", str(text or "").strip()))
    if not raw or _SELF_STATEMENT_RE.match(raw):
        return False
    if not _MEMORY_RECALL_RE.match(raw):
        return False
    # 要有提问形态：尾词 / 问号 / A-not-A / 「有没有」；纯陈述「我上次说过要去杭州」不劫持。
    # 「记住 / 记一下 / 别忘了…」祈使不在 `_MEMORY_VERB` 里，且没有提问形态，天然不进来。
    return any(mark in raw for mark in _QUESTION_MARKS)


#: 记忆读不到时的话术。**不说「没有」**——说不清楚才是真话。两个消费方共用这一句。
MEMORY_UNAVAILABLE_SPEECH = "记忆服务这会儿连不上，我暂时看不到之前记下的内容，稍后再问我一次。"
#: 会话历史读不到时的话术（执行史 / 数据源两条读出口用）。
HISTORY_UNAVAILABLE_SPEECH = "我这会儿查不到这个会话的记录，稍后再试。"
