"""「记住…」——句首的记忆类祈使指令（唯一实现，2026-09-20 从 `orchestrator/cloud/planning.py` 下沉）。

## 它回答什么

这句话是不是用户**直接对助手下的记忆指令**（「记住，我女儿叫小满」「别忘了我不吃香菜」）。
两个消费方：
- planner（R4.4 修正，2026-07-27 真机）：祈使式指令一律视为「在跟助手说话」，不接受模型的
  `addressed=false`——同一句某次 3/3 被拒、换一批又只拒 1 条，是 LLM 方差不是判据问题；
- 会话约束的纯陈述判据（W13 F09-a，2026-09-20）：「记住我喜欢清淡」是要**长期记住**的偏好，
  不是「这次」的口味陈述——评审 §4 最小对比对「记住我喜欢清淡 vs 今天吃清淡一点」正是
  「稳定偏好 vs 本次约束」这一对；把它答成「好的，这次不吃辣」等于把用户的话降级了。

## 为什么住在 runtime/

云侧编排与 `runtime/session_constraints` 都要用同一条，而 runtime 够不着 `orchestrator/cloud`
（镜像依赖闭包，同 `polarity` / `question_shape` 的落点判据）。**只有这一份**，planning 只 import。

须**锚在句首**（去礼貌前缀后）：「我不记得了」「他记住了」不是指令，不能劫持。
"""
from __future__ import annotations

import re

#: 礼貌 / 口头前缀，判定前剥掉。
POLITE_PREFIX_RE = re.compile(r"^[\s，,。.、]*(那|哎|诶|嘿|嗯|请|麻烦|你好|喂)*[\s，,、]*")
#: 记忆类祈使前缀。只覆盖无歧义的那几种（误判代价最大的一类），其余仍由模型判。
MEMORY_DIRECTIVE_RE = re.compile(
    r"^(帮我|给我|你|请)?(记住|记一下|记下来|记下|记着|记得|别忘了|别忘记|别忘)")


def is_memory_directive(text: str | None) -> bool:
    """句首是显式的记忆类祈使指令（「记住…」）。"""
    return bool(MEMORY_DIRECTIVE_RE.match(POLITE_PREFIX_RE.sub("", (text or "").strip())))
