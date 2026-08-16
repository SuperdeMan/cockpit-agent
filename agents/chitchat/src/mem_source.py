"""记忆驱动的回答要说出出处——**确定性后处理，零 LLM**（Q5 残余，2026-08-16）。

## 病不是幻觉，是「真记忆没有出处」

QA 轮把「您女儿在南山实验小学上学」记成幻觉（I-044/I-028）。psql 取证推翻了那个
定性——**库里逐字有这条记忆**。真正的病是：真记忆在用户眼里与幻觉不可区分。
清洗后复跑把它从「方差」改硬成 **0/3 稳定红**。

## 直接成因是系统自己下的指令

`_memory_context` 注入 prompt 时写着「…**勿暴露这是系统记忆**」
——**不是模型忘了说出处，是我们让它别说**。

## 为什么是确定性后处理而不是改提示词

卡上写的是「要的是机制不是提示词」。求 LLM 说出处会漂（同墙钟三件套的既有理由：
系统持有的事实不交给模型答）。这里系统持有的事实是「**这句回答里有没有东西来自记忆**」。

判据分两半，缺一半就退化成假个性化：

1. **回答与某条记忆有足够长的公共内容** —— 说明回答确实用了它；
2. **那段内容不在用户这句话里** —— 否则「你女儿的事我不清楚」也会因为共有「女儿」
   被判成记忆驱动，系统于是声称参考了一条它根本没用的记忆。
   本仓已记过三种假个性化形态（声称参考却没参考 / 把别人的偏好套在你身上 /
   记忆压过当轮明说），**这条判据的第二半就是防第一种**。

⚠ **出处是追加的，不改写原答案**。让模型重说一遍等于把确定性又交回给它。
"""
from __future__ import annotations

from runtime.clock import local_dt

#: 公共子串的最小长度。2 个汉字太容易巧合（「用户」「今天」），3 个起才是内容。
_MIN_SPAN = 3
#: 模型自己已经披露了出处的signature——再追加一句就是两句「您之前提过」。
_SELF_DISCLOSED = ("之前提", "之前说", "您提过", "你提过", "我记得", "记得您",
                   "记得你", "您说过", "你说过", "之前告诉")


def _text_of(mem) -> str:
    if not isinstance(mem, dict):
        return ""
    value = mem.get("text")
    return value if isinstance(value, str) else ""


def _longest_common_span(a: str, b: str) -> str:
    """最长公共子串。短文本上 O(n·m) 完全够用（记忆条目与一句回答都是几十字）。"""
    if not a or not b:
        return ""
    best_end = best_len = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best_len:
                    best_len, best_end = cur[j], i
        prev = cur
    return a[best_end - best_len:best_end]


def memory_evidence(answer: str, question: str, mems) -> dict | None:
    """这句回答里有没有一段**来自记忆、而不是来自用户这句话**的内容。

    命中返回 `{"mem": 那条记忆, "span": 命中的那段文本}`；没有返回 None。
    **确定性纯函数**：同一输入永远同一输出。
    """
    ans, q = str(answer or ""), str(question or "")
    if not ans:
        return None
    for mem in mems or []:
        text = _text_of(mem)
        if not text:
            continue
        span = _longest_common_span(text, ans)
        if len(span) < _MIN_SPAN:
            continue
        # **第二半判据**：这段内容如果用户刚说过，它就不是记忆给的。
        if span in q:
            continue
        return {"mem": mem, "span": span}
    return None


def _recorded_at(mem) -> str:
    """这条记忆是什么时候记的。⚠ 墙钟走 `runtime.clock`——容器 TZ=UTC，
    裸 `fromtimestamp` 会在跨日边界上把日期说错一天（§4.3 时区族）。"""
    if not isinstance(mem, dict):
        return ""
    for key in ("source_ts", "valid_from"):
        raw = mem.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            d = local_dt(float(raw))
            return f"{d.month}月{d.day}日"
    return ""


def with_provenance(answer: str, question: str, mems) -> str:
    """记忆驱动的回答追加出处；**没证据就一个字都不加**。

    「宁可不说，也不要声称参考了没参考的东西」——假个性化比不个性化更伤信任。
    """
    ans = str(answer or "")
    if not ans or any(sig in ans for sig in _SELF_DISCLOSED):
        return ans          # 模型自己说了就别再叠一句
    hit = memory_evidence(ans, question, mems)
    if not hit:
        return ans
    when = _recorded_at(hit["mem"])
    tail = f"（这是您{when}提过的）" if when else "（这是您之前提过的）"
    return ans + tail
