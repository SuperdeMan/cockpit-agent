"""规格值域契约门禁：`input_schema` 声明的组名/项名必须在**真机台账**里出现过。

## 它为什么存在（读之前先看这段）

2026-08-13 引入的 `_SPEC_GROUPS` 是照常见叫法写的：`ice→{冰量,冰度,加冰}`、
`milk→{奶底,奶类,乳基底,奶制品}`、`sweetness→{糖度,甜度}`。2026-08-21 真机一扫，
**瑞幸根本没有「冰量」这一组**（冰/少冰/去冰/热都是「温度」组的取值），奶的真名是
奶基/奶/奶油，美式族的糖度组叫「糖」。三个槽因此**声明齐全却永远匹配不到任何东西**
——用户说「少冰」，系统答「这款饮品不支持"少冰"」，而 HMI 上正画着一个「少冰」chip。

漂移在同一个仓库里躺了两个月没人发现，因为**没有任何一行代码把这两端对起来**：
`_SPEC_GROUPS` 从未被任何测试 import 过，而手写的真机 fixture `_attrs()` 里六个组
（杯型/温度/咖啡豆/咖啡浓度/糖度/奶油）与它一个都对不上。

⇒ **本文件就是那条对齐断言。** 方向单向：声明 ⊆ 台账。台账里有而没声明的不判红
（那只说明「还没有消费方」）。台账由 `scripts/probe_merchant_specs.py` 扫官方接口产出，
是**观测样本不是声明**——要声明一个真实存在但没扫到的组名，正确处置是扩样本重扫，
不是放宽本门禁。放宽它等于把「不许猜组名」这条规矩本身删掉。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from agents.mcp_bridge.src.admission import SPEC_KIND_MERCHANT, load_servers

_BRIDGE = Path(__file__).resolve().parents[1]
_SERVERS = str(_BRIDGE / "servers.yaml")
_LEDGER = _BRIDGE / "knowledge" / "merchant_specs_observed.yaml"


def _normalized_spec(value) -> str:
    """与消费方 `LuckinWorkflow._normalized_spec` **同口径**。

    校验必须复刻消费方的解析（B3 那条）：两边归一方式不同的话，门禁绿而运行时
    照样匹配不上。这里刻意重写一遍而不是 import——它守的正是那个模块。
    """
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()
    return text[:-1] if text.endswith("的") else text


def _ledger() -> dict[str, dict[str, set[str]]]:
    data = yaml.safe_load(_LEDGER.read_text(encoding="utf-8")) or {}
    out: dict[str, dict[str, set[str]]] = {}
    for merchant, body in (data.get("merchants") or {}).items():
        out[str(merchant)] = {
            _normalized_spec(group): {_normalized_spec(item) for item in (items or [])}
            for group, items in (body.get("groups") or {}).items()}
    return out


def _declarations():
    """→ [(merchant, intent, slot, body)]，只取声明了 `input_schema` 的 workflow。"""
    rows = []
    for server in load_servers(_SERVERS):
        for workflow in server.workflows:
            for slot, body in (workflow.input_schema or {}).items():
                rows.append((server.id, workflow.intent, slot, body))
    return rows


def test_ledger_is_present_and_non_empty():
    ledger = _ledger()
    assert ledger, "真机台账为空——没有台账就没有对齐依据，门禁失去意义"
    assert all(groups for groups in ledger.values())


@pytest.mark.parametrize("merchant,intent,slot,body", _declarations(),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_declared_groups_were_observed_on_the_real_merchant(
        merchant, intent, slot, body):
    """声明的官方组名**必须真机见过**——这是 `冰量` 那条 bug 的直接探针。"""
    assert body.get("kind") == SPEC_KIND_MERCHANT
    observed = _ledger().get(merchant) or {}
    assert observed, f"{merchant} 没有真机台账，不能声明 input_schema"
    unseen = sorted(name for name in (body.get("groups") or [])
                    if _normalized_spec(name) not in observed)
    assert not unseen, (
        f"{intent}.{slot} 声明的规格组 {unseen} 在 {merchant} 真机台账里从未出现过"
        "——组名不许靠常见叫法猜。要么改声明，要么扩样本重扫台账")


@pytest.mark.parametrize("merchant,intent,slot,body", _declarations(),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_alias_targets_are_official_item_names(merchant, intent, slot, body):
    """`aliases` 的**键**是官方项名（值才是用户说法），必须在该槽的组里见过。

    方向搞反过一次就会把用户说法当官方名去匹配，症状与 `冰量` 完全一样：
    翻译表看起来配齐了，运行时一条都命中不了。
    """
    observed = _ledger().get(merchant) or {}
    allowed = set()
    for group in (body.get("groups") or []):
        allowed |= observed.get(_normalized_spec(group), set())
    if not (body.get("aliases") or {}):
        return
    assert allowed, f"{intent}.{slot} 声明了 aliases 但它的组在台账里没有任何取值"
    unknown = sorted(name for name in body["aliases"]
                     if _normalized_spec(name) not in allowed)
    assert not unknown, (
        f"{intent}.{slot} 的 aliases 键 {unknown} 不是该组的官方项名"
        "——键必须是**官方项名**，值才是用户说法")


@pytest.mark.parametrize("merchant,intent,slot,body", _declarations(),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_alias_spoken_words_do_not_collide_with_other_official_items(
        merchant, intent, slot, body):
    """用户说法不得**恰好等于另一个官方项名**——那会把用户说的 A 悄悄换成 B。

    「回填错等于系统替用户改了他说的话」（`runtime/slot_fidelity` 那条）的翻译版：
    若有人把 `少甜: [微甜]` 写进别名表，用户说「微甜」就会下成「少甜」，
    而两者都是商家真实存在的档位——**这种错在真栈上完全看不出来**。
    """
    observed = _ledger().get(merchant) or {}
    official = set()
    for group in (body.get("groups") or []):
        official |= observed.get(_normalized_spec(group), set())
    for target, spoken in (body.get("aliases") or {}).items():
        target_norm = _normalized_spec(target)
        collide = sorted(word for word in spoken
                         if _normalized_spec(word) in official
                         and _normalized_spec(word) != target_norm)
        assert not collide, (
            f"{intent}.{slot} 把官方项 {collide} 当成了 “{target}” 的别名"
            "——那是替用户改档位，不是翻译")


def test_every_declared_slot_is_a_real_capability_slot():
    """声明一个 planner 永远填不到的槽等于没声明（判据同 `candidate_slot`）。"""
    for server in load_servers(_SERVERS):
        for workflow in server.workflows:
            declared = set(workflow.slots or [])
            extra = sorted(set(workflow.input_schema or {}) - declared)
            assert not extra, f"{workflow.intent} 的 input_schema 声明了非槽位 {extra}"


def test_same_group_slots_have_distinct_precedence():
    """指向同一个官方组的多个槽必须能**确定性地**分出胜负。

    瑞幸的 `temperature` 与 `ice` 刻意同组（冰/少冰/去冰/热都在「温度」里）。
    真栈实测「来一杯冰美式去冰」planner 同时产两个槽——若 precedence 相同，
    最终落哪一项就取决于遍历顺序，那是巧合不是判据。
    """
    for server in load_servers(_SERVERS):
        for workflow in server.workflows:
            by_group: dict[frozenset, list[tuple[str, int]]] = {}
            for slot, body in (workflow.input_schema or {}).items():
                key = frozenset(_normalized_spec(name)
                                for name in (body.get("groups") or []))
                by_group.setdefault(key, []).append(
                    (slot, int(body.get("precedence") or 0)))
            for key, rows in by_group.items():
                if len(rows) < 2:
                    continue
                ranks = [rank for _, rank in rows]
                assert len(set(ranks)) == len(ranks), (
                    f"{workflow.intent} 有多个槽 {[s for s, _ in rows]} 指向同一组 "
                    f"{sorted(key)}，但 precedence 分不出胜负")


def test_declared_groups_are_identical_or_disjoint():
    """任意两个槽的 `groups` **要么相同要么不相交**——`_spec_order` 的隐含前提。

    消费方按「声明的 groups 集合」给槽分组，再用 `precedence` 决胜。若有人写出
    `sweetness: [糖度, 糖]` 与 `sugar: [糖]` 这种**部分重叠**的声明，两个槽会被算成
    不同组、却在运行时落到同一个官方组：先写的被后写的悄悄覆盖，**没有任何一处会报错**
    ——正是本文件要消灭的那种形态（同 `冰量` 那条：每一层看起来都在正常工作）。

    ⚠ 这条不变量原本只活在实现的注释里。**可选的断言等于托付给人记得**（B1），
    所以把它写成门禁而不是写成一句提醒。
    """
    for server in load_servers(_SERVERS):
        for workflow in server.workflows:
            rows = [(slot, {_normalized_spec(name)
                            for name in (body.get("groups") or [])})
                    for slot, body in (workflow.input_schema or {}).items()]
            for i, (slot_a, groups_a) in enumerate(rows):
                for slot_b, groups_b in rows[i + 1:]:
                    shared = groups_a & groups_b
                    assert not shared or groups_a == groups_b, (
                        f"{workflow.intent} 的 {slot_a} 与 {slot_b} 的 groups 部分重叠"
                        f"（共有 {sorted(shared)}）——消费方按声明集合分组，"
                        "部分重叠会让两个槽在运行时落到同一个官方组却互不知情")
