"""B6 §5 第 2 条：ActionabilityClassifier 与对抗语料的离线回放取证。

**这是 B6 存在的第一性理由，不成立就撤。** 判据原文：「`nq.landmark.bare` 合并族
在分类器下的 CLARIFY 命中率显著高于 planner 现状」。

但只量召回是不够的——真正的风险在**假阳性**：一个把什么都判成 CLARIFY 的分类器
在这三条上是满分，代价是把整个语料变成反问机器。所以本回放同时量两侧：

- **召回**：金标 `decision.clarify=required` 的用例（当前 3 条）判成 CLARIFY 的比例；
- **假阳性**：金标要求出计划（即该执行）的用例被判成 CLARIFY 的比例，**分母是整份
  对抗语料**（500+ 条唯一输入）。

对照物是 planner 现状：`nq.landmark.bare` 两次无干预读数合并 **11/20 ≈ 55%**
（语料头注与 findings §18 的原始读数）。用 Fisher 精确检验比。

零 LLM、零网络、纯离线——它量的是形态判据，不是模型。
用法：`python test/eval_actionability.py [--json out.json]`
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from orchestrator.cloud.actionability import Actionability, classify   # noqa: E402
from support.intent_adversarial_contract import load_cases             # noqa: E402

_CORPUS = _ROOT / "test" / "eval_corpus" / "intent_adversarial" / "cases"

# planner 现状对照（`nq.landmark.bare`，两次无干预读数合并；语料头注 / findings §18）
_PLANNER_BASELINE = (11, 20)


class _Focus:
    def __init__(self, last_intent=""):
        self.last_intent = last_intent


def _focus_of(context: dict):
    """语料 context 里声明过上一轮意图时，把它当结构焦点喂给分类器。"""
    focus = ((context or {}).get("focus") or {}) if isinstance(context, dict) else {}
    last_intent = str(focus.get("last_intent") or "")
    if not last_intent and isinstance(context, dict):
        last_intent = str(context.get("last_intent") or "")
    return _Focus(last_intent) if last_intent else None


def _fisher_p(a: int, b: int, c: int, d: int) -> float:
    """2x2 Fisher 精确检验（双侧）。表小，直接枚举。"""
    def _c(n, k):
        return math.comb(n, k)

    row1, row2 = a + b, c + d
    col1, total = a + c, a + b + c + d
    denom = _c(total, col1)
    observed = _c(row1, a) * _c(row2, c) / denom
    p = 0.0
    for i in range(max(0, col1 - row2), min(row1, col1) + 1):
        prob = _c(row1, i) * _c(row2, col1 - i) / denom
        if prob <= observed + 1e-12:
            p += prob
    return min(1.0, p)


def evaluate() -> dict:
    cases = load_cases(_CORPUS)
    clarify_gold, execute_gold = [], []
    for case in cases:
        for turn in case.turns:
            expected = turn.expected
            wants_clarify = (expected.clarify == "required"
                             or tuple(expected.decision_allowed) == ("clarify",))
            row = {"case": case.id, "utterance": turn.utterance}
            if wants_clarify:
                clarify_gold.append(row | {"context": turn.context})
            else:
                # 假阳性的分母是**除澄清金标之外的全部轮**，不是「有 plan 金标的轮」。
                # 首版按后者算，把 `ei.*` 端侧 ingress 用例（「暂停」「锁车门」——
                # 恰恰是零谓述标记的裸动词短语）整批挡在分母外，读出来是漂亮的
                # 0/472。**分母挑得越干净，假阳性越好看**，这正是该防的那种自欺。
                execute_gold.append(row | {"context": turn.context})

    hits, misses = [], []
    for row in clarify_gold:
        verdict = classify(row["utterance"], focus=_focus_of(row["context"]))
        (hits if verdict.decision is Actionability.CLARIFY else misses).append(
            {**{k: row[k] for k in ("case", "utterance")},
             "decision": verdict.decision.value,
             "confidence": round(verdict.confidence, 2)})

    false_positives = []
    for row in execute_gold:
        verdict = classify(row["utterance"], focus=_focus_of(row["context"]))
        if verdict.decision is Actionability.CLARIFY:
            false_positives.append(
                {**{k: row[k] for k in ("case", "utterance")},
                 "confidence": round(verdict.confidence, 2)})

    # 判据的射程是**裸对象族**（`nq.landmark.bare` / `nq.city.bare`）。
    # `ex.colloquial.dark`「有点看不清路了」金标也要澄清，但它不是裸对象——
    # 它是「说清了状态、没说清补救动作」的另一族，形态上与「有点热」（金标执行）
    # 逐字同构。**分开算，不合并成一个好看的总分。**
    bare = [row for row in (hits + misses) if row["case"].endswith(".bare")]
    bare_hits = sum(1 for row in bare if row["decision"] == "clarify")
    base_hit, base_n = _PLANNER_BASELINE
    return {
        "clarify_gold": len(clarify_gold),
        "clarify_hits": len(hits),
        "clarify_misses": misses,
        "bare_family": {"hits": bare_hits, "n": len(bare)},
        "execute_gold": len(execute_gold),
        "false_positives": false_positives,
        "false_positive_rate": (len(false_positives) / len(execute_gold)
                                if execute_gold else 0.0),
        "planner_baseline": {"hits": base_hit, "n": base_n,
                             "case": "nq.landmark.bare"},
        # ⚠ 这个 p 的分母是**人为的**：分类器是确定性的，同一条输入判 20 次还是
        # 判 2000 次都一样，重复次数不是独立样本。把它算出来只是为了与 planner 那
        # 20 个**真**样本摆在同一张表上；真正的内容是「零方差命中 vs 45% 漏判」，
        # 不是这个小数点。
        "fisher_p_sample_matched": _fisher_p(
            base_n, 0, base_hit, base_n - base_hit),
        "hits": hits,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    report = evaluate()

    bare = report["bare_family"]
    print(f"裸对象族召回 : {bare['hits']}/{bare['n']}（判据的射程就是这一族）")
    print(f"全部澄清金标 : {report['clarify_hits']}/{report['clarify_gold']}")
    for miss in report["clarify_misses"]:
        print(f"   [miss] {miss['case']:24s} {miss['utterance']} -> {miss['decision']}")
    print(f"假阳性       : {len(report['false_positives'])}/{report['execute_gold']} "
          f"({report['false_positive_rate']:.2%})  ← 真正的闸在这里")
    for fp in report["false_positives"][:20]:
        print(f"   [fp]   {fp['case']:24s} {fp['utterance']}")
    base = report["planner_baseline"]
    print(f"planner 对照 : {base['hits']}/{base['n']}（{base['case']}，"
          f"两次无干预读数合并）")
    print(f"Fisher p     : {report['fisher_p_sample_matched']:.2e}"
          f"  ⚠ 分母人为——确定性判据重复多少次都一样，真正的内容是零方差 vs 45% 漏判")

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
