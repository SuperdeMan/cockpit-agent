"""「这句话是不是在声称系统做了什么」——执行性声明的**形态**判据（QA C11-C，2026-08-28）。

## 它要抓的形态

真栈 family T21：用户说「从深圳欢乐海岸出发」，这一轮落到 chitchat，模型答
「好的，**已经为您**重新计算路线，从华侨城欢乐海岸出发，不走高速，全程大约1.6公里」
——零 navigation 调用、零动作，探针 `fails=[]`。同族 info T48 的「为您规划路线：…
请确认是否发起导航？」、T43 的自我纠错道歉都是同一件事：**话里有一次执行，账上没有。**

## 判据是形态，不是词表

只认**指向用户的完成/进行体标记**（「已为您」「正在帮您」「…好了」），
**不认识任何领域词**——没有路线、订单、提醒、商户、车控对象。源码级断言
`test_execution_claim.py::test_no_domain_words_in_source` 钉着这条：判据一旦开始
认识「路线」，它就变成了一张永远补不完的禁语清单（C11-A 记的正是这笔账——
禁语清单式的防编造 prompt 每轮 QA 都会被绕过一次）。

## 消费方与纪律

- **只写观测、不进决策**（B6 `actionability` 的同款 shadow 纪律）：编排层在 final
  唯一出口按它 + 「本轮 actions 为空」出一位 obs 列，两周真实分布出来再谈拦截。
- 已知误报面（先量、别猜）：① 信息类能力的合法完成声明（行程/调研这类不出 action
  的域，「已为您规划3天行程」是真的）；② 转述历史或复述用户的话。
  **误报的代价只是一位观测，漏报的代价是缺陷继续隐形。**
- 探针（C16-2）用的必须是这一份，不许在 `scripts/` 抄第二张表——判据抄两份
  就会长出「这边认得那边不认得」的分歧（同 `runtime/session_facts.py` 那条）。
"""
from __future__ import annotations

import re
import time

#: 「为/帮/替/给 + 您/你」——**指向用户**的服务体标记。执行性声明与客观陈述的分界线
#: 就在这里：「路线已经算好了」是陈述，「已经为您算好了路线」是声称自己做过。
_FOR_YOU = r"(?:为|帮|替|给)\s*[您你]"

#: 完成体：已/已经 + 指向用户，或「为您…好了/完成/完毕」。
#: ⚠ **两支都要求指向用户的标记**。曾经有第三支「已经…好了」（不带「为您」），
#: 它把「路线已经算好了吗？」这类**客观陈述/反问**一并判成声明——判据一旦不看
#: 「是谁做的」，它量的就不再是「系统声称自己动了手」。
#: 代价是漏掉「路线已经算好了。」这种无主句声明，**这是刻意选的那一侧**：
#: shadow 的分布要能读，宁可窄。
_DONE_RE = re.compile(
    rf"已经?\s*{_FOR_YOU}"
    rf"|{_FOR_YOU}[^，。！？；、\s]{{0,10}}(?:好了|完成了?|完毕)")

#: 进行体/承诺体：正在/这就/马上/稍后 + 指向用户。「将要做什么」与「已经做了什么」
#: 同罪——用户听到的都是「系统这一轮动了」。
_ONGOING_RE = re.compile(rf"(?:正在|这就|马上|立刻|稍后|接下来)\s*{_FOR_YOU}")


def execution_claim(text: str | None) -> str:
    """返回命中的形态族名（`done` / `ongoing`），没有返回空串。

    两族分开报而不是合成一个布尔：它们的误报面不同（完成体多来自信息类能力的
    合法完成语，进行体几乎只出现在兜底路径的承诺里），合成一列就分不开了
    ——同「复合判据的诊断出口要拆开报」那条（android-m3 批）。
    """
    t = (text or "").strip()
    if not t:
        return ""
    if _DONE_RE.search(t):
        return "done"
    if _ONGOING_RE.search(t):
        return "ongoing"
    return ""


#: 句边界（W14 按句剥）：只认句末标点与换行，零领域词。
_SENTENCE_END_RE = re.compile(r"[。！？!?；;\n]")
#: 分句边界：超长句里丢一段声称时，丢到它所在分句的结束处（逗号 / 顿号 / 冒号，全角半角）。
_CLAUSE_END_RE = re.compile(r"[，,、：:]")
#: 三条分支合成一份，`search` 给的就是**最左**那一处声称（流式与一次喂入都用它）。
_CLAIM_RE = re.compile(rf"(?:{_DONE_RE.pattern})|(?:{_ONGOING_RE.pattern})")
#: 一处声称从起点算最多跨多少个**非空白**字（三条分支里最长的是「为您 + ≤10 字 + 完成了」= 15）。
#: 超长句分段释放时保留末尾这么多个非空白字——任何可能在后面补全的声称，起点都落在保留区里，已放出去的
#: 前缀永远不会成为某处声称的一部分。`test_stream_claim_gate` 从三条正则本身推导这个数并与之对账：
#: 改了正则忘了改这里会红。
CLAIM_SPAN_MAX = 15
#: 谈话步的声称被剥空之后给用户的诚实话术（零领域词；engine 的 W14 出口与流式出口共用这一句）。
CLAIM_STRIPPED_SPEECH = "这一轮我没有执行任何操作。要我做什么的话，说具体一点，我来安排。"


def is_execution_claim_sentence(sentence: str) -> bool:
    """一句话是不是执行性声称（流式闸与 `strip_execution_claims` 共用同一条判据）。"""
    return bool(_CLAIM_RE.search(sentence or ""))


def _holdback_start(text: str) -> int:
    """`text[i:]` 恰好含 `CLAIM_SPAN_MAX` 个非空白字的那个 i（不够就是 0）。"""
    count = 0
    for i in range(len(text) - 1, -1, -1):
        if not text[i].isspace():
            count += 1
            if count >= CLAIM_SPAN_MAX:
                return i
    return 0


class ExecutionClaimGate:
    """流式 speech 的执行性声称闸（评审二轮 R4 → 三轮 R3-04：**放行只由文本决定，与切包无关**）。

    二轮版的有界释放是「缓冲超过 160 字且到目前为止不像声称 ⇒ 整段放」：160 字无标点前缀 +「已」一包、
    「为您关闭车窗。」一包 ⇒ 第一包放了、第二包没有「已」也放了，合起来正是「已为您关闭车窗」；同一文本一次喂入
    却会被拦，final 再按整句剥又是第三种结果。三轮规则：

    - **≤ 160 字的句子**（含终止符）整句判：不是声称整句放，是声称整句丢（与二轮相同，首字等一句）；
    - **超长句**进分段模式：只放出「保留区」之前的前缀（保留末尾 `CLAIM_SPAN_MAX` 个非空白字，后到的字补全
      不出一处起点在已放部分的声称）；一处声称成立 ⇒ 丢掉**从它起点到所在分句结束**的那一段，其余照放；
      句末标点照放；
    - 一次喂入与任意切包给出**逐字相同**的输出（`test_stream_claim_gate` 的性质测试钉着）；已放出的字不可撤回，
      `released` 就是用户实际收到的全部文本——final / 落库以它为准，不再对全文换一套分句重剥。

    判据与 final 那一份同源（`is_execution_claim_sentence`）。只该挂在**按声明不可能执行**的步上（response_only）——
    信息类能力的「已为您规划 3 天行程」是真的。`first_hold_ms`：第一个增量进门到第一次放行的等待（首字时延的代价，量出来而不是承诺）。
    """

    def __init__(self, limit: int = 160, clock=None):
        self._buf = ""
        self._limit = max(1, int(limit))
        self._long = False          # 当前句已进分段模式
        self._dropping = False      # 分段模式里正丢着一段声称，等分句边界
        self.removed = 0
        self.released = ""
        self._clock = clock or time.monotonic
        self._first_in: float | None = None
        self._first_out: float | None = None

    @property
    def first_hold_ms(self) -> float | None:
        if self._first_in is None or self._first_out is None:
            return None
        return max(0.0, (self._first_out - self._first_in) * 1000.0)

    def _emit(self, text: str) -> str:
        if text:
            self.released += text
            if self._first_out is None:
                self._first_out = self._clock()
        return text

    def _judge_long(self, text: str, final: bool) -> tuple[str, str]:
        """分段模式：返回 `(这次放出的, 仍未判定的)`。`final` = 这句已经完整（带终止符或流末）。"""
        out: list[str] = []
        t = text
        while t:
            if self._dropping:
                boundary = _CLAUSE_END_RE.search(t)
                if boundary is None:
                    if final:
                        if _SENTENCE_END_RE.fullmatch(t[-1]):
                            out.append(t[-1])            # 丢的是那一段声称，不是这句话的收尾
                        self._dropping = False
                    return "".join(out), ""
                t = t[boundary.end():]
                self._dropping = False
                continue
            cut = len(t) if final else _holdback_start(t)
            claim = _CLAIM_RE.search(t)
            if claim is not None and claim.start() < cut:
                out.append(t[:claim.start()])
                self.removed += 1
                self._dropping = True
                t = t[claim.end():]
                continue
            out.append(t[:cut])
            t = t[cut:]
            break
        return "".join(out), t

    def _close_sentence(self, part: str) -> str:
        """一句完整了（带终止符，或流末的尾巴）。"""
        if self._long or len(part) > self._limit:
            released, _rest = self._judge_long(part, final=True)
            self._long = False
            self._dropping = False
            return released
        if is_execution_claim_sentence(part):
            self.removed += 1
            return ""
        return part

    def feed(self, delta: str | None) -> str:
        if delta and self._first_in is None:
            self._first_in = self._clock()
        self._buf += delta or ""
        out: list[str] = []
        while True:
            end = _SENTENCE_END_RE.search(self._buf)
            if end is None:
                break
            part, self._buf = self._buf[:end.end()], self._buf[end.end():]
            out.append(self._close_sentence(part))
        if self._buf and (self._long or len(self._buf) > self._limit):
            self._long = True
            released, self._buf = self._judge_long(self._buf, final=False)
            out.append(released)
        return self._emit("".join(out))

    def flush(self) -> str:
        """流结束：没标点的尾巴按一句判。"""
        tail, self._buf = self._buf, ""
        return self._emit(self._close_sentence(tail) if tail else "")


def strip_execution_claims(text: str | None) -> tuple[str, int]:
    """把话术里**声称执行**的部分剥掉 → `(剩下的话术, 剥掉的处数)`（评审 W14，2026-09-20）。

    评审三轮 R3-04：就是 `ExecutionClaimGate` 一次喂入——判据与分段规则只有一份实现，流式放出的、final 的、
    落库的三份文本因此对得上。只在调用方已经证明「这一轮按声明不可能执行任何事」时才该调它（谈话步 + 零动作）。
    剥空了由调用方换 `CLAIM_STRIPPED_SPEECH`，这里不编话。
    """
    t = (text or "").strip()
    if not t:
        return "", 0
    gate = ExecutionClaimGate()
    out = gate.feed(t) + gate.flush()
    if not gate.removed:
        return t, 0
    cleaned = re.sub(r"\n{2,}", "\n", out.strip()).strip()
    return cleaned, gate.removed
