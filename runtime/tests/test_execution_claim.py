"""`runtime/execution_claim` 的两向断言 + 「判据零领域词」的源码级钉子（C11-C）。

它是 shadow（只写观测不进决策），但**误伤面仍然要写下来**：一位观测列如果 90%
是误报，两周之后没人读得懂那份分布。所以下面的「不该命中」那一半里，
既有真实的信息类完成语（`nearby` 的「为您找到 10 家」），也有客观陈述
（「路线已经算好了」——它没有指向用户的服务体标记）。
"""
from __future__ import annotations

import ast
import inspect
import os
import re

import pytest
import yaml

from runtime import execution_claim as mod
from runtime.execution_claim import execution_claim

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_COMMANDS = os.path.join(
    _ROOT, "orchestrator", "edge", "knowledge", "commands.yaml")


# ── 1. 完成体：真栈原句 ────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    # family T21 chitchat 编造改路线（探针 fails=[]，人工漏检兜出来的）
    "好的，已经为您重新计算路线，从华侨城欢乐海岸出发，不走高速，全程大约1.6公里。",
    # demo-mkemhn 那批堵过的交易话术，形态一样
    "已为您找到 10 家门店，请选择其中一家。",
    "已经帮您把提醒设置好了。",
    "已替您安排完成。",
    # ⚠ 2026-08-29 补四条**真栈原句**（QA 余项 ③/④ 的取证跑批，deployed `ed53f8f`）：
    # 「取消<事项>」这类不点名域的句子有 45% 落错域，其中一族落到兜底闲聊，
    # 而它会**零动作地宣称已经取消了**。这四条是 47 条干净会话样本里逐字捞出来的。
    # 判据对它们四条全部命中 ⇒ **shadow 是对的，只是还没到拦截的时候**
    # （模块 docstring 写着「两周真实分布出来再谈拦截」，它 2026-08-28 才上线）。
    # 留在这里的意义：把一次现场取样变成常驻回归探针——判据日后收窄时会当场报红。
    "好的，已为你取消代号17879685843的评审会。",
    "好的，已为你取消参加代号17879685843的评审会。",
    "好的，已为您取消参加代号17879685843的评审会。",
])
def test_completed_execution_claims_are_detected(text):
    assert execution_claim(text) == "done"


# ── 2. 进行体/承诺体 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "正在为您重新规划路线，请稍等。",
    "这就帮您安排。",
    "马上给您接通。",
    # 同批真栈原句（进行体那一支）：零动作却承诺「这就帮你取消」，
    # 还顺带编了一段「取消后原定议程就清掉了」的后果说明。
    "好的，这就帮你取消代号17879688782的评审会。不过提醒一下，取消后原定议程就清掉了，"
    "要是之后还想聊这次会议的内容，我这边查不到任何记录啦。",
])
def test_ongoing_execution_claims_are_detected(text):
    assert execution_claim(text) == "ongoing"


# ── 3. 不许误伤（这一半和上一半一样重要）──────────────────────────────────

@pytest.mark.parametrize("text", [
    "",
    "为您找到 10 家川菜，推荐川胖虎、辣宴、老四川印象。",   # 信息类答复，没有完成体
    "路线已经算好了吗？",                                    # 客观陈述 + 疑问，无服务体标记
    "今天深圳小雨，气温28℃。",
    "您说过不吃辣，这次没按平时的川菜找。",
    "抱歉，我这边没有查询能力，您可以说「查询附近的瑞幸咖啡」。",
    "好的，靠边停车后注意打开双闪。",
])
def test_plain_answers_are_not_execution_claims(text):
    assert execution_claim(text) == ""


# ── 4. 判据零领域词（源码级钉子）──────────────────────────────────────────

def _domain_vocabulary() -> set[str]:
    """VAL 知识库派生的领域词——手抄的那份迟早漂移，漂了这条断言就等于不存在。"""
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


def _judgment_code() -> str:
    """模块源码**剥掉 docstring**（注释由 ast 天然丢弃）——docstring 里正当地写着
    「路线」「订单」这些领域词，裸扫源码会把说明文字读成判据（第 3 批那笔账）。"""
    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None) or []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_domain_vocabulary_probe_is_not_empty():
    """先证明这条断言扫得到东西——空集合会让它永远绿。"""
    vocab = _domain_vocabulary()
    assert len(vocab) > 50, f"知识库派生词表只有 {len(vocab)} 条，扫描口径不对"
    assert "空调" in vocab


def test_claim_predicate_contains_no_domain_word():
    """判据里出现任何领域词 = 它从「形态」退化成了禁语清单——而禁语清单式的
    防编造判据每轮 QA 都会被绕过一次（C11-A 记的正是这笔账）。

    ⚠ ASCII 词按**词边界**匹配、中文词按子串（第 3 批：`dec` 是 `declared` 的子串）。
    """
    body = _judgment_code()
    for word in _domain_vocabulary():
        if re.fullmatch(r"[A-Za-z0-9_]+", word):
            hit = re.search(rf"\b{re.escape(word)}\b", body) is not None
        else:
            hit = word in body
        assert not hit, (
            f"execution_claim 判据里出现了 VAL 领域词 `{word}`——"
            f"「说了做没做」必须是形态量，不能认识具体对象")
