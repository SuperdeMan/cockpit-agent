"""对象桥接表门禁（M5 P3 收尾）：三套对象命名之间的裁定台账。

系统里同一个对象有三套名字——语料标签（NLU 输出）／VAL objects（`commands.yaml`）／
规则对象（`fast_intent` 输出，95 种、38 种不在 VAL 里）。表把它们归成等价类。

守四条，形态照抄 `skills/exemplars/boundaries.yaml` 的裁定台账：**人裁一次，机器只负责
「不许悄悄漏」**。
  ① 语料里出现过的每个标签都必须被裁定过——新语料引入新标签时当场红，而不是运行期
     静默变成 `unmapped`（那样影子会悄悄少一批样本，没人发现）；
  ② 表里引用的每个名字都必须真的存在（VAL object，或规则在全量语料上真吐得出来的名字）
     ——写错**不报错只变差**，`agree` 永不成立而没人知道（「没消费方的契约会潜伏」）；
  ③ 语料已消失的标签不许滞留（只进不出会腐烂）；
  ④ 可执行子集必须是**派生**的（等价名 ∩ VAL objects），不许另写一列。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import nlu as edge_nlu
from val import VAL

_ROOT = Path(__file__).resolve().parents[3]
_CORPUS = _ROOT / "test" / "eval_corpus" / "feishu_intents_full.jsonl"
_BRIDGE = Path(__file__).resolve().parents[1] / "knowledge" / "nlu_objects.yaml"


def _bridge() -> dict:
    return (yaml.safe_load(_BRIDGE.read_text(encoding="utf-8")) or {}).get("objects") or {}


def _val_objects() -> set[str]:
    return set((VAL().commands or {}).get("objects") or {})


def _corpus_rows() -> list[dict]:
    with _CORPUS.open(encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _corpus_objects() -> set[str]:
    return {r["object"] for r in _corpus_rows() if r.get("object")}


def test_every_corpus_object_is_adjudicated():
    missing = sorted(_corpus_objects() - set(_bridge()))
    assert not missing, (
        f"语料里这些对象还没在 nlu_objects.yaml 里被裁定：{missing}。"
        "请对着语料原文裁定（不是按名字相似度猜）——确实没有对应名就登记空列表。")


def test_no_stale_entries():
    """表里不许留语料已经没有的标签——只进不出会腐烂（boundaries 台账同款纪律）。"""
    stale = sorted(set(_bridge()) - _corpus_objects())
    assert not stale, f"这些标签语料里已不存在，应删除：{stale}"


def test_referenced_names_all_exist_somewhere():
    """引用的名字要么是 VAL object，要么是规则**真吐得出来**的对象名。

    第二半用全量语料跑一遍 `classify_structured` 取实际输出集合，而不是读 fast_intent
    源码里的字面量——**要验的是它真会产生什么，不是它看起来会产生什么**。
    """
    from fast_intent import classify_structured
    known = _val_objects()
    assert known, "VAL 知识库没加载到，本测试失去意义"
    emitted = set()
    for r in _corpus_rows():
        s = classify_structured(r["text"])
        if s:
            obj = (s.get("data") or {}).get("object", "")
            if obj:
                emitted.add(obj)
    allowed = known | emitted
    bad = {label: [o for o in (objs or []) if o not in allowed]
           for label, objs in _bridge().items()
           if any(o not in allowed for o in (objs or []))}
    assert not bad, f"引用了既不是 VAL object、规则也吐不出来的名字：{bad}"


def test_val_subset_is_derived_by_intersection():
    known = _val_objects()
    assert edge_nlu.val_objects("轮胎", known) == ["tire_pressure_monitoring"]
    # 规则侧专名不该混进可执行子集
    assert "tire_pressure" not in edge_nlu.val_objects("轮胎", known)
    # 商用车能力：等价名有（规则认得），VAL 可执行子集为空
    assert edge_nlu.equivalent_objects("采暖") == ["step_heating"]
    assert edge_nlu.val_objects("采暖", known) == []


@pytest.mark.parametrize("label,expect_contains", [
    ("空调模式/功能控制", "aircon"),
    ("座椅/儿童座椅", "seat"),
    ("天窗/天窗遮阳帘", "sunroof"),
    ("车窗", "window"),
    ("辅助驾驶", "lane_departure_assistance"),
    ("辅助驾驶", "blind_spot_warning"),          # 规则侧专名也在同一个等价类里
    ("能源", "energy_recovery"),
    ("湿度", "humidity"),
])
def test_lookup(label, expect_contains):
    got = edge_nlu.equivalent_objects(label)
    assert got and expect_contains in got


def test_lookup_distinguishes_unknown_from_no_equivalent():
    assert edge_nlu.equivalent_objects("这个标签不存在") is None      # 待裁定
    assert edge_nlu.equivalent_objects("工具") == []                 # 已裁定：无对应名


def test_corpus_adjudicated_labels_not_guessed_by_name():
    """两条按名字猜必错、对着语料原文才裁得对的，留作回归。

    - `温度`：188 条全是「查深圳市的温度」，是天气不是空调；
    - `声音`：**我第一版就猜错过**——列表头部 4 条恰好都是「打开声音设置界面」，于是
      裁成了 page；全量看 62% 是音量（导航/媒体/电话音量、静音）。
      **只看头部的抽样偏差方向是固定的**（同 P2 hint 退役那次「抽样→全覆盖」）。
    """
    assert "aircon" not in (edge_nlu.equivalent_objects("温度") or [])
    assert "weather" in (edge_nlu.equivalent_objects("温度") or [])
    assert "volume" in (edge_nlu.equivalent_objects("声音") or [])


def test_known_rule_defect_is_not_laundered_into_agreement():
    """`指数` 不许收 `stock`。

    规则把「查深圳的穿衣指数」判成股指（179 条 100% 命中 `stock`）——那是**规则的错**，
    不是命名差异。收进等价类就等于把一个真 badcase 洗成 `agree`：影子从此看不见它。
    桥接表的职责是消除命名差异，**不是消除分歧**。
    """
    assert "stock" not in (edge_nlu.equivalent_objects("指数") or [])
