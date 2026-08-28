"""会话内**当轮陈述的偏好/忌口**——抽取判据与词表的唯一实现（QA C12-B，2026-08-28）。

## 它解决什么

真栈 info T28→T29：用户先说「我不吃辣，也不想排长队」（落 chitchat），下一句
「推荐附近适合晚饭的地方」拿到的是**10 家川菜**，话术还三句自相矛盾
（「按您的口味优先川菜」+「不合口味的已排后」+「记得您说过不吃辣」）。

成因不是判据错，是**没有载体**：`nearby` 只读得到 `intent.raw_text`（当轮），
而「不吃辣」是**上一轮**说的；唯一的跨轮通道是异步记忆抽取绕 PG 一圈，
几秒之内未必落库。§4.3 的「记忆是背景，当轮说的是前景」写下一年了，
**没有 session_constraints 这个载体它就只是一句话**。

## 为什么住在 runtime/

两个消费方够不着彼此：抽取点在**云侧编排**（`extract_focus`，云侧镜像没有
`agents/`），消费点在 **nearby Agent**（端上的 SDK 镜像没有 `orchestrator/`）。
落点判据是镜像依赖闭包——同 `polarity` / `clause_split` / `session_facts` 那几笔。

## 语义：最新一次说的算

`no_spicy=True`（说了忌口）与 `no_spicy=False`（**明确要辣**）都是事实，
按轮次覆盖。刻意不做成「只进不出的闩」：用户改主意是正常的，而一个永远解除不了的
忌口会把「今天想吃点辣的」变成系统跟他犟嘴。**否定优先于肯定判**——
「不想吃辣」里也含「想吃辣」，先判否定这条顺序就是语义本身（同 N9 那条
「词表分支序就是语义」）。
"""
from __future__ import annotations

import re

#: 忌辣说法：**原话、记忆文本、会话约束三处共用**。首版（在 nearby 里）只认
#: 「不…吃/沾辣」，真栈实测「不要太辣」根本不匹配——用户当轮明说的忌口连识别
#: 都没识别到，记忆里的川菜偏好照样把检索词改成「川菜」。
NO_SPICY_RE = re.compile(
    r"(?:不|(?<!特)别|少|忌|怕)(?:能|要|想|太|吃|沾|放|加|了)*辣|清淡")
#: 明确要辣：**只在没命中忌辣时才看**（顺序即语义，见模块 docstring）。
WANT_SPICY_RE = re.compile(r"(?:想|要|来|吃|点)(?:点|些|个|份)?辣|重口|越辣越")
#: 不排队：记下来但**不假装能筛**——地图没有实时排队数据，消费方要如实说这条按不上。
#: ⚠ 「排**长**队」是 2026-08-28 补的：真栈 T28 的原话正是「也不想排长队」，
#: 而旧词表要求否定词紧贴「排队」二字 ⇒ 用户明说的第二条约束**连识别都没识别到**。
#: 与当年「不要太辣」漏掉是同一形态——**词表要按人真的怎么说来写，不是按判据好写来写**。
NO_QUEUE_RE = re.compile(
    r"(?:不喜欢|不爱|讨厌|嫌|怕|不愿意?|不想|别|不用|不)\s*(?:排\s*(?:长|大)?队|等位)"
    r"|排队少")
#: 重辣菜系词：判「这个检索词/这家店辣不辣」，与上面三条正则是**同一件事的两面**，
#: 所以住在同一个模块里（消费方 nearby 的降权与话术都读它）。
SPICY_MARKS = ("川菜", "湘菜", "火锅", "串串", "麻辣烫", "冒菜", "麻辣")


def constraints_in(text: str | None) -> dict:
    """一句话里的会话级偏好约束 → `{"no_spicy": bool, "no_queue": True}`（缺省不写键）。

    只写**说出来的那些键**：没提到辣就不写 `no_spicy`，让上层的跨轮合并
    保住上一次的表态。「没说」和「说了不要」必须分得开——合成一个 False
    正是 `day_offset_of` 那条老账（认不出与说的是今天分不开）的同族形态。
    """
    t = (text or "").strip()
    if not t:
        return {}
    out: dict = {}
    if NO_SPICY_RE.search(t):
        out["no_spicy"] = True
    elif WANT_SPICY_RE.search(t):
        out["no_spicy"] = False
    if NO_QUEUE_RE.search(t):
        out["no_queue"] = True
    return out


def merge_constraints(previous: dict | None, current: dict | None) -> dict:
    """跨轮合并：**后说的覆盖先说的**，没说的沿用。返回新 dict，不改入参。"""
    out = {k: v for k, v in (previous or {}).items() if isinstance(k, str)}
    out.update({k: v for k, v in (current or {}).items() if isinstance(k, str)})
    return out
