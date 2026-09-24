"""纯应答判据：这句话只是在应答（「好的」「可以」），或复述了一句助手口吻的执行声称（「已为您执行」）——本身不带任何请求。

两个消费方读这一份（追加批 I，2026-09-24；设计 §12）：

- **engine 的确认授权**：肯定词表 = 本模块的 `ACK_WORDS` + 确认 / 确定 / 下单 / 支付 / 选定类（`orchestrator/cloud/engine.py`）；
  「语气面只能修饰、不能独立授权」（评审三轮 R3-01 A）的剥离循环也在这里——`consists_of` 记着有没有吃到一个词；
- **planner 出口**：原话是纯应答、上一轮助手又没有提议 ⇒ 计划里不许有写步（`orchestrator/cloud/planning.py`）。
  `a4bb73bf` RS21「可以，已为您执行」第一轮就被规划成 `reminder.cancel {index:"2"}`，碰巧没有第 2 条提醒。

「就这家 / 下单 / 支付 / 订吧」不是应答：它们在候选列表之后本身就是请求，只在确认授权那一侧算肯定。
零领域词：全是应答词、语气词与标点；执行声称用 `runtime.execution_claim` 那一份。
"""
from __future__ import annotations

import re

from runtime.execution_claim import is_execution_claim_sentence

#: 应答词：只表示「听到了 / 同意」，本身不带任何要做的事。
ACK_WORDS = ("好的", "好啊", "可以", "是的", "嗯", "行", "好", "ok")
#: 裸确认的语气面（评审二轮 R1，2026-09-22）：肯定词之外**只允许**语气尾 / 承接虚词 / 礼貌前缀 / 标点。
#: 剥完必须一个实质字都不剩——「行程」的「程」、「确认函」的「函」、「可以改」的「改」都不是语气尾。
#: 旧判据给裸确认留了 2 字松弛（`len(t) <= len(k)+2`），它本来是给语气尾留的，却把任何 ≤2 字的
#: 实质尾巴一并当成了授权（局部复算：三句都判 `kind=one`）。
#: ⚠ 评审三轮 R3-01 A（2026-09-23）：语气面**只能修饰**。剥完而一个肯定词都没吃到（「啊 / 唉 / 请 / 那 / 。」）
#: 不是授权——`consists_of` 记着有没有消费过肯定词。「嗯」是肯定词（产品口径），所以不在这张语气表里：
#: 留着它会先被当语气剥掉，「嗯」单说反而不算确认。
PARTICLE_RE = re.compile(
    r"^(?:请|麻烦|那就|那|就|一下|吧|呀|啊|呢|哦|噢|喔|啦|嘛|哈|喽|咯|哟|呗|哎|唉|的|了"
    r"|[，,、。！!？?~\s])+")
_ACK_BY_LEN = tuple(sorted(ACK_WORDS, key=len, reverse=True))
_CLAUSE_SPLIT_RE = re.compile(r"[，,、。．.！!？?；;~～\s]+")


def consists_of(text: str | None, words_by_len: tuple[str, ...]) -> bool:
    """整句是否只由 `words_by_len` 里的词 + 语气面组成，**且至少吃到一个词**（`words_by_len` 须按长度降序）。

    「好的，确认吧」「嗯可以」「行啊」→ True；「行程」「确认函」「好像不对」→ False；「啊 / 唉 / 请 / 那 / 。」→ False。
    """
    core = (text or "").strip().lower()
    consumed = False
    while core:
        core = PARTICLE_RE.sub("", core)
        if not core:
            break
        for word in words_by_len:
            if core.startswith(word):
                core = core[len(word):]
                consumed = True
                break
        else:
            return False
    return consumed


def is_bare_acknowledgment(text: str | None) -> bool:
    """整句只是一个应答（`ACK_WORDS` + 语气面，至少一个应答词），不含确认 / 下单 / 支付 / 选定类。

    评审四轮 R4-01（2026-09-24）：「好的 / 嗯 / 可以 / 行」回答的是**最近那一问**，可能是一次闲聊提议（「还要再听一个吗」），
    不必是某个事务确认。engine 据此把它与「确认」分开：没有挂起时它不被答成「当前没有待确认的操作」；有待确认的挂起时，
    只有那条挂起就是最近那个提示才算授权（修前真栈：挂着「打开后备箱」、插话听了个笑话后说「好的」，后备箱被打开）。
    「好的，确认吧」「嗯确认」带事务词，不在此列。
    """
    return consists_of(text, _ACK_BY_LEN)


#: 提议句式（评审四轮 R4-01）：助手在**主动提出要替用户做一件事**——「要不要我帮你…」「需要我…吗」「我帮你…好吗」。
#: 零领域词，全是提议框架；知识问句（「你知道为什么…吗」）、续讲确认（「还要再听一个吗」）不在此列。
OFFER_MARKERS = ("要不要", "需不需要", "是否需要", "要不我", "需要我", "要我", "我帮你", "我帮您", "我来帮",
                 "我可以帮", "可以帮你", "可以帮您", "帮你", "帮您", "我给你", "我给您", "我来为你", "我来为您")
_QUOTED_SPAN_RE = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|‘[^’]*’|\"[^\"]*\"")
_SENTENCE_RE = re.compile(r"[^。！!？?\n]+[。！!？?]*")


def offer_sentence(text: str | None) -> str:
    """助手这句话的**最后一句**若是提议问句（引号里的不算）⇒ 返回那一句；否则空串。

    纯应答写步闸据此判「好的」接受的是哪一项（评审四轮 R4-01）：修前只看最近一条助手话里有没有问号——问号可能来自引用
    （「您刚才问的是「怎么打开车窗？」」）或知识问句，不是可执行提议的证明。提议必须是最后一句：前面说完一件事、最后
    才问下一步的，接受的是最后那一问。
    """
    body = _QUOTED_SPAN_RE.sub("", text or "")
    sentences = [s.strip() for s in _SENTENCE_RE.findall(body) if s.strip()]
    if not sentences:
        return ""
    last = sentences[-1]
    asks = last.endswith(("？", "?")) or last.rstrip("。！!~ ").endswith(("吗", "呢", "么"))
    return last if asks and any(marker in last for marker in OFFER_MARKERS) else ""


def is_acknowledgment_only(text: str | None) -> bool:
    """每个分句要么只是应答（`ACK_WORDS` + 语气面），要么是助手口吻的执行声称——整句没有任何请求。

    「可以，已为您执行」「好的」「嗯，正在为您处理」→ True；「好的，打开空调」「取消导航」「就这家」「谢谢」「啊」→ False。
    """
    clauses = [clause for clause in _CLAUSE_SPLIT_RE.split((text or "").strip()) if clause]
    if not clauses:
        return False
    return all(consists_of(clause, _ACK_BY_LEN) or is_execution_claim_sentence(clause)
               for clause in clauses)
