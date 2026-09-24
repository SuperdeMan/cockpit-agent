"""「这一句话点名了这一步吗」——**唯一实现**（评审四轮，2026-09-24）。

两个消费方读这一份：

- **问句闸按分句归属**（R4-03，`planning._question_side_effect_steps`）：「打开后备箱，再告诉我空调有哪些模式」整句判成问句后，
  修前删掉全部写步 / 需确认步——后备箱确认卡不出。现在每一步看**点名它的是哪一类分句**：指令分句点得比任何提问分句都准才保留；
- **纯应答接受提议**（R4-01，`planning._apply_ack_write_guard`）：「好的」只接受助手最后那句提议点名的那一步。

点名的依据是**能力描述**（Registry 装配进 `Step.capability_description`，LLM 写不到）与**槽值**：
描述的功能主干（第一个冒号 / 分号 / 句号 / 括号 / 逗号之前——后面是写给规划器的用法说明）去掉零领域操作词后的**对象部分**，
或任一 ≥2 字槽值，在这句话里有 ≥2 字片段 ⇒ 点名；分数 = 主干里被这句话覆盖的字数 + 出现在这句话里的槽值字数，只在「谁点得更准」时比较。

⚠ 操作词表只收 **≥2 字**的动词：单字「调 / 开 / 关」会从名词里挖字（「空调」的「调」），对象部分就只剩一个「空」——
追加批 E / F 两次栽在同一个坑上（「空调有什么模式」被判成指令）。零领域：表里全是动词，不认识任何对象。
"""
from __future__ import annotations

import re

from runtime.question_shape import HOW_TO_ACTIONS, is_imperative_opening

#: 能力描述与指令常用的多字操作词（端侧 `capabilities._describe` 写的动词 + 问句判据的方法动词里 ≥2 字的那些）。
OPERATION_WORDS = tuple(sorted({w for w in HOW_TO_ACTIONS if len(w) >= 2} | {
    "关上", "合上", "收起", "调高", "调低", "调到", "调成", "调节", "调大", "调小", "设为", "设成", "升高", "降低",
    "查询", "播放", "暂停", "停止", "继续", "折叠", "展开", "切换到下一个", "切换到上一个", "切换", "取消",
    "创建", "修改", "删除", "开始", "结束", "退出", "恢复", "锁上", "解锁"}, key=len, reverse=True))
_HEAD_SPLIT_RE = re.compile(r"[：:；;。（(，,\n]")
_PUNCT_RE = re.compile(r"[\s，,、。．.！!？?~～·…:：;；\"'“”‘’「」『』《》〈〉（）()\[\]【】\-—_/|*]+")
#: 分句开头的承接 / 礼貌成分（剥掉后再看是不是以指令起句）。零领域虚词。
_LEADING_FILLER_RE = re.compile(r"^(?:再|然后|接着|随后|顺便|顺带|并且|同时|还有|另外|先|也|都|就|请|麻烦|帮我|给我|替我|你|您)+")


def _compact(text: str) -> str:
    return _PUNCT_RE.sub("", str(text or ""))


def description_head(description: str) -> str:
    """能力描述的功能主干（「打开后备箱」「停车缴费（**真的把钱付出去**…）」⇒「停车缴费」）。"""
    d = str(description or "").strip()
    return _HEAD_SPLIT_RE.split(d, maxsplit=1)[0].strip() if d else ""


def object_core(description: str) -> str:
    """主干去掉多字操作词之后剩下的对象部分（「调高空调温度」⇒「空调温度」）。"""
    core = description_head(description)
    for word in OPERATION_WORDS:
        core = core.replace(word, " ")
    return _compact(core)


def _covered(needle: str, haystack: str) -> int:
    """`needle` 里被 `haystack` 的 ≥2 字片段覆盖的字数。"""
    n, h = _compact(needle), _compact(haystack)
    covered = [False] * len(n)
    for i in range(len(n) - 1):
        for j in range(len(n), i + 1, -1):
            if n[i:j] in h:
                for k in range(i, j):
                    covered[k] = True
                break
    return sum(covered)


def naming_score(text: str, description: str, slots: dict | None = None) -> int:
    """这句话点名这一步的分数；0 = 没点名（对象部分与槽值都不在这句话里）。"""
    t = _compact(text)
    if not t:
        return 0
    core = object_core(description)
    values = [str(v).strip() for v in (slots or {}).values()
              if isinstance(v, str) and len(str(v).strip()) >= 2]
    hit_values = [v for v in values if _compact(v) and _compact(v) in t]
    named = bool(hit_values) or any(core[i:i + 2] in t for i in range(len(core) - 1))
    if not named:
        return 0
    return max(1, _covered(description_head(description), t) + sum(len(_compact(v)) for v in hit_values))


#: 开 / 关方向词。只收多字：单字「关」会撞上「关于」、单字「开」会撞上「开心 / 开车」。
OPEN_WORDS = ("打开", "开启", "开一下")
CLOSE_WORDS = ("关闭", "关掉", "关上", "合上", "关一下", "关了")
_OPEN_OPERATIONS = frozenset({"open", "on", "unfold"})
_CLOSE_OPERATIONS = frozenset({"close", "off", "fold"})


def inverts_stated_direction(text: str, step) -> bool:
    """这句话明确说了开（或关），而这一步是反方向——「关闭空调」点到了「空调」，但它不是 `hvac.on` 的依据。"""
    says_open = any(word in str(text or "") for word in OPEN_WORDS)
    says_close = any(word in str(text or "") for word in CLOSE_WORDS)
    if says_open == says_close:
        return False
    operation = str(getattr(step, "intent", "") or "").rsplit(".", 1)[-1]
    return (says_open and operation in _CLOSE_OPERATIONS) or (says_close and operation in _OPEN_OPERATIONS)


def step_naming_score(text: str, step) -> int:
    """这句话点名这一步的分数；方向说反了不算点名（0）。"""
    if inverts_stated_direction(text, step):
        return 0
    return naming_score(text, str(getattr(step, "capability_description", "") or ""),
                        getattr(step, "slots", None) or {})


def opens_as_instruction(clause: str) -> bool:
    """这个分句**以指令起句**：去掉承接 / 礼貌成分后以多字操作词开头，或是「把 / 将」处置式 /「请 / 麻烦」礼貌祈使。

    认不出的一律不算（问句闸那边就按修前整句拦）——「空调开到26度」这种对象在前的指令会少认，那是保守那一侧的代价；
    「不是打开后备箱」「先不打开」以否定 / 纠正开头，不以指令起句，天然不算。
    """
    c = _compact(clause)
    if not c:
        return False
    if is_imperative_opening(clause):
        return True
    body = _LEADING_FILLER_RE.sub("", c)
    return any(body.startswith(word) for word in OPERATION_WORDS)
