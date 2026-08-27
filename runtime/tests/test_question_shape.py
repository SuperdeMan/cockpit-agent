"""`runtime/question_shape` 的两向断言 + 「判据零领域词」的源码级钉子。

这份判据 2026-08-27 从 `orchestrator/edge/fast_intent` 下沉到 runtime，理由写在模块
docstring 里（云侧要用同一条，而云侧镜像够不着 edge）。下沉的那一刻起它变成了
**安全闸的输入**（云侧「问句 + 写车控步」守卫），所以它自己也要有两向覆盖：
挡住的那一半和**不许误伤**的那一半各写一遍——收窄/放宽面只写一边守不住。
"""
from __future__ import annotations

import os

import pytest
import yaml

from runtime.question_shape import (
    CAPABILITY_ASKS, DIRECTIVE_MARKERS, HYPOTHETICAL_FRAMES, MANNER_ASKS,
    OPERATION_VERBS, PROPERTY_ASKS, QUESTION_TAILS, is_non_directive_question,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_COMMANDS = os.path.join(_ROOT, "orchestrator", "edge", "knowledge", "commands.yaml")

_CLASSES = {
    "QUESTION_TAILS": QUESTION_TAILS,
    "CAPABILITY_ASKS": CAPABILITY_ASKS,
    "PROPERTY_ASKS": PROPERTY_ASKS,
    "MANNER_ASKS": MANNER_ASKS,
    "HYPOTHETICAL_FRAMES": HYPOTHETICAL_FRAMES,
    "DIRECTIVE_MARKERS": DIRECTIVE_MARKERS,
}


def _domain_vocabulary() -> set[str]:
    """VAL 知识库里的领域词（对象 id / 中文名 / intent 段）。

    从知识库派生而不是手抄——手抄那份迟早与知识库漂移，
    而这条断言一旦漂移就等于不存在（同 `test_actionability` 的做法）。
    """
    with open(_COMMANDS, encoding="utf-8") as handle:
        commands = yaml.safe_load(handle) or {}
    vocab: set[str] = set()
    for name, spec in (commands.get("objects") or {}).items():
        vocab.add(str(name))
        display = str((spec or {}).get("display_name") or "").strip()
        if display:
            vocab.add(display)
        for intent in ((spec or {}).get("edge_intents") or []):
            vocab.update(str(intent).split("."))
    return {word for word in vocab if word}


# ── 1. 判据零领域词 ────────────────────────────────────────────────────────

def test_domain_vocabulary_probe_is_not_empty():
    """先证明这条断言扫得到东西——空集合会让它永远绿。"""
    vocab = _domain_vocabulary()
    assert len(vocab) > 50, f"知识库派生词表只有 {len(vocab)} 条，扫描口径不对"
    assert "空调" in vocab


def test_no_feature_word_is_domain_vocabulary():
    """任一特征词撞上领域词 = 这份「形态判据」已经退化成对象特判。

    它守的是云侧那道安全闸的**性质**：闸的判据必须与「这句话在说哪个对象」无关，
    否则 R2.1「不在编排核心加领域字面量」那条铁律就在安全面上被绕过去了。
    """
    vocab = _domain_vocabulary()
    for name, words in _CLASSES.items():
        for word in words:
            assert word not in vocab, (
                f"{name} 里的 `{word}` 是 VAL 领域词——判据必须是句法/形态量")


def test_operation_verbs_are_single_char_function_words():
    """操作动词表是**单字**闭类；一旦有人往里加「打开天窗」这类词，这条当场红。"""
    for verb in OPERATION_VERBS:
        assert len(verb) == 1, f"OPERATION_VERBS 里的 `{verb}` 不是单字动词"


# ── 2. 是提问（写操作必须被挡） ────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "这车的天窗最大能开多大",
    "红色机油灯亮了怎么办",
    "车窗能不能自己关",
    "要是天窗一直开着会怎么样",
    "后备箱能开吗？",
    "空调最低多少度",
    "双闪什么时候用",
])
def test_questions_are_recognised(text):
    assert is_non_directive_question(text) is True, text


# ── 3. 不是提问（祈使句不许被误伤） ────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "打开天窗",
    "帮我把车窗关一下",          # 礼貌请求带疑问壳，仍是指令
    "能帮我关下车窗吗",
    "请把空调调到 24 度",
    "温度如何调高",              # 方式问法 + 操作动词 ⇒ 仍是指令
    "怎么把座椅加热打开",
])
def test_directives_are_not_mistaken_for_questions(text):
    assert is_non_directive_question(text) is False, text


def test_manner_ask_without_operation_verb_is_a_question():
    """`MANNER_ASKS` 只有在**不带操作动词**时才算提问——这一对是同一条判据。"""
    assert is_non_directive_question("这个功能为什么会自己触发") is True
    assert is_non_directive_question("为什么要把温度调那么高") is False
