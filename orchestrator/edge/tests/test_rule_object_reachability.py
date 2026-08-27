"""端侧规则吐得出的对象，必须是 VAL 知识库认识的对象（2026-08-27，QA N8）。

## 这条门禁为什么是新的

B4 那套能力完整性门禁逐条跑的是 **`edge_call.decode_intent`**（云侧下发那条路），
它产的对象名一律从 `commands.yaml` 的 `edge_intents` 反解，**结构上不可能对不上**。
而端侧快路径的产出方是另一个：`fast_intent._classify_structured` 直接把对象名写进
结构化命令，再交给 `VAL._validate_command`。**同一个 intent 有两个产出方，门禁只走了
其中一条**——这句话上一次出现是 QA I-004（方向盘加热的 `operate` 对不上），
`test_classifier_exit_parity.py` 为此补了 VAL 校验断言；但那条按**金标文本**逐句走，
一个从没进过金标的对象照样漏。

N8 就是这么活下来的：规则产 `tire_pressure`，知识库声明的对象叫
`tire_pressure_monitoring`，于是**每一句「胎压是多少」都秒回「暂不支持哦」**，
而 `nlu_objects.yaml` 的等价类台账早就把这处分歧记在册上了——
**记录一个缺陷不等于修它**（§4.3）。

## 判据形态

按**产出方**盘点，不按语料盘点：从源码里静态取出全部 `_s(...)` 的对象名（AST，不是正则），
逐个走**唯一实现** `_to_legacy_name` 得到意图名；只要它 `is_local`（= 端侧会自己执行，
不上云），那个对象就必须在 `commands.yaml` 里声明——否则 VAL 一定拒，用户一定听到
「暂不支持哦」。语料盘点做不到这件事：没人给这个对象写过语料，正是它能活下来的原因。

## 已知缺口台账

下面四条是本次取证顺带扫出来的**同族存量**（不是本批修的那条）。它们逐条写明
「说什么话会踩到」，而不是一个通配豁免——同 `capability_exemptions.yaml` 的口径：
禁通配符、必须写 reason。**修好一条就把它从表里删掉，表空了就把这段注释也删掉。**
新增第五条会让本文件当场红：那正是它存在的理由。
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fast_intent  # noqa: E402
from val import VAL  # noqa: E402

_EDGE_DIR = pathlib.Path(__file__).resolve().parent.parent

#: 对象名 → (踩到它的一句话, 为什么还没修)。**逐对象逐条，禁止通配。**
_KNOWN_UNREACHABLE = {
    "factory_settings": (
        "恢复出厂设置",
        "整车级破坏性动作，能力面从未声明；补声明前要先定 require_confirm 与权限，"
        "不是补一行 YAML 的事",
    ),
    "launcher": (
        "返回桌面",
        "HMI 导航动作，落点应是 hmi 域而不是 VAL 车控对象；改落点比补对象正确",
    ),
    "memory": (
        "清理内存",
        "系统维护动作（intent 名已经是 system.clean），同上：落点存疑，"
        "补一个 VAL 对象只会把错落点固化",
    ),
    "sound_effect": (
        "音效调成摇滚",
        "与已声明的 equalizer 是同一件事的两个名字（『把音效设成人声』走 equalizer "
        "就能执行）；正解是合并到 equalizer，不是再声明一个对象",
    ),
}


def _declared_objects() -> set[str]:
    path = _EDGE_DIR / "knowledge" / "commands.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return set((document.get("objects") or {}).keys())


def _rule_emitted_objects() -> dict[str, set[str]]:
    """源码里 `_s(domain, intent, operate, object, ...)` 的对象名 → 它会变成的意图名。

    静态取而不是跑语料：**能活下来的缺口恰恰是没人给它写过语料的那些。**
    只收四个位置实参都是字面量的调用；变量拼出来的对象名不在此列（当前一个也没有，
    真出现时这张表会少一个成员，而不是给出一个错的成员）。
    """
    source = (_EDGE_DIR / "fast_intent.py").read_text(encoding="utf-8")
    emitted: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_s"):
            continue
        args = node.args[:4]
        if len(args) < 4 or not all(
                isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args):
            continue
        domain, intent, operate, obj = (a.value for a in args)
        name = fast_intent._to_legacy_name({
            "domain": domain, "intent": intent, "_raw_text": "",
            "data": {"operate": operate, "object": obj},
        })
        if name:
            emitted.setdefault(obj, set()).add(name)
    return emitted


def _locally_executed_objects() -> dict[str, set[str]]:
    return {
        obj: {n for n in names if fast_intent.is_local(n)}
        for obj, names in _rule_emitted_objects().items()
        if any(fast_intent.is_local(n) for n in names)
    }


def test_every_locally_executed_rule_object_is_declared():
    """端侧自己执行的对象必须在知识库里——否则用户听到的是「暂不支持哦」。"""
    declared = _declared_objects()
    missing = {obj: sorted(names)
               for obj, names in _locally_executed_objects().items()
               if obj not in declared}
    unexpected = {obj: names for obj, names in missing.items()
                  if obj not in _KNOWN_UNREACHABLE}
    assert not unexpected, (
        f"这些对象规则产得出、`LOCAL_INTENTS` 也收着，但 `commands.yaml` 没声明："
        f"{unexpected}。端侧会当场执行并被 VAL 拒掉，用户听到「暂不支持哦」；"
        f"云侧下发那条路不会踩到它（decode_intent 从声明反解），"
        f"所以 B4 门禁是绿的。要么补声明，要么把它移出 LOCAL_INTENTS 让它上云。")


def test_known_unreachable_ledger_has_no_stale_rows():
    """修好一条就删一行——**恒绿的豁免表比没有更糟**。"""
    declared = _declared_objects()
    locally_executed = _locally_executed_objects()
    stale = sorted(obj for obj in _KNOWN_UNREACHABLE
                   if obj in declared or obj not in locally_executed)
    assert not stale, (
        f"台账里这些行已经不成立了（对象已声明，或规则不再本地执行它）：{stale}。"
        f"删掉它们，别让豁免表替一个已经修好的洞继续开口。")


@pytest.mark.parametrize("obj", sorted(_KNOWN_UNREACHABLE))
def test_known_unreachable_rows_still_reproduce(obj):
    """台账上的每一行都要**当场复现**，不能只是一句传说。

    这条断言把「我们知道它坏」变成一次真实执行：说那句话、走真实结构化路径、
    读 VAL 的回答。哪天它不再复现，上面那条 stale 断言会红，人就会来看这里。
    """
    utterance, _reason = _KNOWN_UNREACHABLE[obj]
    structured = fast_intent.classify_structured(utterance)
    assert structured is not None, f"{utterance!r} 现在认不出来了，台账要更新"
    assert structured["data"].get("object") == obj
    ok, speech = VAL(knowledge_dir=str(_EDGE_DIR / "knowledge")).execute(structured)
    assert not ok and "暂不支持" in speech, (
        f"{utterance!r} 现在能执行了（{speech!r}）——把 {obj} 从台账里删掉")
