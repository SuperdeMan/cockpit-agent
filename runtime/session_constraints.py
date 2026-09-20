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

## 2026-09-19（评审 F05 / W03）：按分句、带时态与主体

评审局部复算抓到三个洞：`no_queue` 只有 True 通道（「今天可以排队」撤不掉）；整句否定
优先把「之前不吃辣，今天想吃辣」压成 `no_spicy=True`；「同行的人想吃辣」会覆盖说话人
自己的忌口。修法不是加三条特例，是把抽取单位从**整句**改成**分句**（`runtime.clause_split`
那一份分隔符表），每个分句各自判：

- **时态框架**：分句里有「之前 / 以前 / 原来 / 上次…」且没有「今天 / 现在 / 这次…」⇒ 转述
  过去，不写键；
- **主体框架**：分句里点名了别人（同行的人 / 朋友 / 老婆…）且不含「我们 / 我也…」⇒ 记在
  `others` 子键下，绝不覆盖说话人自己的键；
- **撤销**：「辣不辣无所谓 / 排不排队都行」⇒ 写 `None`，`merge_constraints` 遇 None 删键；
- `no_queue` 增加 False 通道（「可以排队 / 排队也行 / 不介意排队 / 等位也可以」）。

扁平投影 `{"no_spicy": bool, "no_queue": bool}` 的契约不变，消费方（nearby）照旧只读
自己认识的键；`others` 是新增子键，不认识它的消费方逐字零行为变化。
"""
from __future__ import annotations

import re

from runtime.clause_split import split_clauses
from runtime.memory_directive import is_memory_directive

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
#: 可以排队（`no_queue=False` 的通道，W03）。两种形态：「（不介意/可以/能/愿意/不怕）排队」
#: 与「排队（也行/可以/没关系/没问题/无所谓）」。`(?<!不)` 挡「不能排队」。
OK_QUEUE_RE = re.compile(
    r"(?<!不)(?:可以|能|愿意|不介意|不怕|不在乎|接受)\s*"
    r"(?:排(?:一会儿|会儿|点)?队|等位|等一会儿?|等等)"
    r"|(?:排(?:一会儿|会儿|点)?队|等位|等一会儿?)\s*(?:也|都)?"
    r"(?:行|可以|没关系|没问题|无所谓|不要紧|ok|OK)")
#: 撤销：这一维**不再是约束**（写 None ⇒ 合并时删键）。
WAIVE_SPICY_RE = re.compile(r"辣不辣(?:都行|都可以|无所谓|没关系|都无所谓|都没关系)|不用管辣不辣")
WAIVE_QUEUE_RE = re.compile(r"排不排队(?:都行|都可以|无所谓|没关系|都无所谓|都没关系)|不用管排不排队")
#: 时态框架：分句在**转述过去**（「之前不吃辣」）——不是当前约束。当前框架在场时照常算数。
PAST_FRAME_RE = re.compile(r"之前|以前|原来|上次|上回|过去|先前|从前")
NOW_FRAME_RE = re.compile(r"今天|现在|这次|这回|今晚|这顿|目前|今儿|这会儿|还是")
#: 主体框架：分句说的是**别人**的约束（「同行的人想吃辣」）。含「我们 / 我也 / 我和…」时
#: 说话人也在内，仍写自己的键。零领域词：全是人称与关系称谓。
OTHERS_FRAME_RE = re.compile(
    r"同行|同事|朋友|老婆|老公|媳妇|太太|先生|爸|妈|孩子|家人|他们|她们|别人|其他人|一起的人|家里人")
SELF_INCLUDED_RE = re.compile(r"我们|我也|我和|我跟|我与|大家都?")
#: 重辣菜系词：判「这个检索词/这家店辣不辣」，与上面三条正则是**同一件事的两面**，
#: 所以住在同一个模块里（消费方 nearby 的降权与话术都读它）。
SPICY_MARKS = ("川菜", "湘菜", "火锅", "串串", "麻辣烫", "冒菜", "麻辣")


def _clause_facts(clause: str) -> dict:
    """一个分句里说出来的键。值 True/False 是事实，None 是撤销；没提的键不出现。"""
    out: dict = {}
    if WAIVE_SPICY_RE.search(clause):
        out["no_spicy"] = None
    elif NO_SPICY_RE.search(clause):
        out["no_spicy"] = True
    elif WANT_SPICY_RE.search(clause):
        out["no_spicy"] = False
    if WAIVE_QUEUE_RE.search(clause):
        out["no_queue"] = None
    elif OK_QUEUE_RE.search(clause):
        # 接受式先于否定式：「不怕排队」「不介意排队」里的「怕/不」会命中否定词表
        out["no_queue"] = False
    elif NO_QUEUE_RE.search(clause):
        out["no_queue"] = True
    return out


def constraints_in(text: str | None) -> dict:
    """一句话里的会话级偏好约束 → `{"no_spicy": bool|None, "no_queue": bool|None,
    "others": {...}}`（缺省不写键）。

    只写**说出来的那些键**：没提到辣就不写 `no_spicy`，让上层的跨轮合并
    保住上一次的表态。「没说」和「说了不要」必须分得开——合成一个 False
    正是 `day_offset_of` 那条老账（认不出与说的是今天分不开）的同族形态。

    W03 起按分句判（见模块 docstring）：后面的分句覆盖前面的（「不吃辣，算了辣也行」
    以后者为准），转述过去的分句跳过，别人的约束进 `others`。
    """
    t = (text or "").strip()
    if not t:
        return {}
    out: dict = {}
    for clause in split_clauses(t):
        if PAST_FRAME_RE.search(clause) and not NOW_FRAME_RE.search(clause):
            continue
        facts = _clause_facts(clause)
        if not facts:
            continue
        if OTHERS_FRAME_RE.search(clause) and not SELF_INCLUDED_RE.search(clause):
            out.setdefault("others", {}).update(facts)
        else:
            out.update(facts)
    return out


def is_pure_constraint_statement(text: str | None) -> bool:
    """这句话**只**在陈述偏好 / 忌口（评审 W13 F09-a，2026-09-20）。

    判据：每个分句都说出了某个键（`_clause_facts` 非空），包括转述过去与撤销的分句——
    「之前不吃辣，今天想吃辣」整句都在谈口味。任何一个分句谈的是别的（「我不吃辣，帮我找家餐厅」）
    ⇒ 不是纯陈述，交回正常规划。真栈三批四次：这类句子在 MiniMax-M3 下 3/4 落技术失败出口，
    而登记本身早就成功——一句陈述换来一句报错，缺的只是一句确定性的致谢。
    """
    t = (text or "").strip()
    if not t:
        return False
    # 「记住我喜欢清淡」是要长期记住的偏好（评审 §4 对比对「稳定偏好 vs 本次约束」），
    # 不是本次口味的陈述：交给正常规划 / 记忆抽取，不在这里答成「这次…」。
    if is_memory_directive(t):
        return False
    clauses = split_clauses(t)
    return bool(clauses) and all(_clause_facts(c) for c in clauses)


#: 扁平键的人话（唯一词表）：焦点块与致谢话术共用，改词只改这里。
_PHRASES = {
    ("no_spicy", True): "不吃辣",
    ("no_spicy", False): "想吃辣",
    ("no_queue", True): "不想排队",
    ("no_queue", False): "可以排队",
}
_WAIVER_PHRASES = {"no_spicy": "辣不辣都行", "no_queue": "排不排队都行"}


def phrase_of(key: str, value) -> str:
    """`(键, 值)` → 人话；`None`（撤销）→「X 都行」；认不出返回空串。"""
    if value is None:
        return _WAIVER_PHRASES.get(str(key), "")
    return _PHRASES.get((str(key), bool(value)), "")


def describe_constraints(constraints: dict | None) -> list[str]:
    """投影里说话人自己的键 → 人话列表（声明序），不含 `others`。"""
    out: list[str] = []
    for key in ("no_spicy", "no_queue"):
        if key in (constraints or {}):
            phrase = phrase_of(key, constraints[key])
            if phrase:
                out.append(phrase)
    return out


def merge_constraints(previous: dict | None, current: dict | None) -> dict:
    """跨轮合并：**后说的覆盖先说的**，没说的沿用，`None` 删键。返回新 dict，不改入参。

    `others` 子键同规则递归合并；合并后为空的子键整个去掉——消费方读到的永远是
    「说过且仍有效」的那些键。
    """
    out: dict = {}
    for source in (previous or {}, current or {}):
        for k, v in source.items():
            if not isinstance(k, str):
                continue
            if k == "others":
                sub = dict(out.get("others") or {})
                for sk, sv in (v or {}).items() if isinstance(v, dict) else ():
                    if sv is None:
                        sub.pop(sk, None)
                    else:
                        sub[sk] = sv
                if sub:
                    out["others"] = sub
                else:
                    out.pop("others", None)
            elif v is None:
                out.pop(k, None)
            else:
                out[k] = v
    return out
