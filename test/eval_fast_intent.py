"""T3.4① 端侧 fast_intent 意图路由评测：以现有 corpus + 历史回归案例为标注集，输出
准确率/召回率报告（JSON+Markdown），可与已入库基线比对（跌破即告警、不阻塞）。

数据来源（只读）：
  - orchestrator/edge/tests/corpus/vehicle_objects.yaml :: intent_recognition
  - orchestrator/edge/tests/corpus/multi_intent.yaml :: split / no_split
  - test/eval_corpus/edge_regressions.yaml :: positive / hijack_guard

不改动 fast_intent.py 任何业务逻辑；不替代原 pytest（test_corpus_objects.py /
test_corpus_multi_intent.py / test_fast_intent_extended.py 继续独立运行，是第一道防线）。

用法：
  python test/eval_fast_intent.py                  # 跑一次，和已入库基线比对（不阻塞）
  python test/eval_fast_intent.py --write-baseline  # 生成/覆盖基线（人工审阅后提交）
  python test/eval_fast_intent.py --strict          # 有回归时 exit 1（v1 未在 CI 启用）

飞书 1465 意图库标注语料当前不可得（原始表已 gitignore 且磁盘不存在，见
docs/design/2026-07-03-r3.4-intent-eval-baseline.md §2）；本脚本的标注集规模以现有
corpus + 历史回归案例为准，报告「数据来源」一节如实标注。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:                                   # Windows 控制台默认 GBK，强制 UTF-8 输出（同 e2e_ws.py 惯例）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_common import (  # noqa: E402
    build_report, diff_against_baseline, load_baseline, print_ci_annotations,
    render_markdown, write_report, CaseResult,
)

sys.path.insert(0, os.path.join(str(_ROOT), "orchestrator", "edge"))
from fast_intent import classify_structured, split_and_classify_any  # noqa: E402

_VEHICLE_OBJECTS = _ROOT / "orchestrator" / "edge" / "tests" / "corpus" / "vehicle_objects.yaml"
_MULTI_INTENT = _ROOT / "orchestrator" / "edge" / "tests" / "corpus" / "multi_intent.yaml"
_EDGE_REGRESSIONS = _ROOT / "test" / "eval_corpus" / "edge_regressions.yaml"
_DEFAULT_BASELINE = _ROOT / "docs" / "reviews" / "eval" / "baseline_fast_intent.json"
# R4.1 P2：全量飞书语料覆盖率报告（--corpus full）
_FULL_CORPUS = _ROOT / "test" / "eval_corpus" / "feishu_intents_full.jsonl"
_COVERAGE_JSON = _ROOT / "docs" / "reviews" / "eval" / "coverage_fast_intent.json"


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _object_recognition_cases() -> tuple[list[CaseResult], list[dict]]:
    """object_recognition 桶：自然语句 -> object 是否识别正确（多分类：漏判/错判/整体正确率）。"""
    cases: list[CaseResult] = []
    sources: list[dict] = []

    vehicle_objects = _load_yaml(_VEHICLE_OBJECTS)
    intent_recognition = vehicle_objects["intent_recognition"]
    for c in intent_recognition:
        text = c["text"]
        expected = c["object"]
        structured = classify_structured(text)
        actual = structured["data"].get("object") if structured else None
        passed = (actual in expected) if isinstance(expected, list) else (actual == expected)
        cases.append(CaseResult(
            id=f"object_recognition::{text}", bucket="object_recognition", text=text,
            expected=expected, actual=actual, passed=passed,
            source=f"{_VEHICLE_OBJECTS.relative_to(_ROOT).as_posix()}::intent_recognition",
        ))
    sources.append({"path": f"{_VEHICLE_OBJECTS.relative_to(_ROOT).as_posix()}::intent_recognition",
                     "count": len(intent_recognition)})

    regressions = _load_yaml(_EDGE_REGRESSIONS)
    positive = regressions.get("positive", [])
    for c in positive:
        text = c["text"]
        expected = c["expect_object"]
        structured = classify_structured(text)
        actual = structured["data"].get("object") if structured else None
        cases.append(CaseResult(
            id=f"object_recognition::{text}", bucket="object_recognition", text=text,
            expected=expected, actual=actual, passed=(actual == expected),
            source=c.get("source", ""), tags=c.get("tags", []),
        ))
    sources.append({"path": f"{_EDGE_REGRESSIONS.relative_to(_ROOT).as_posix()}::positive", "count": len(positive)})

    return cases, sources


def _object_recognition_guardrail_cases() -> tuple[list[CaseResult], list[dict]]:
    """object_recognition_guardrail 桶：不应命中的用例是否真的没被误判（二元通过率，不与
    上面的多分类指标混合平均——两者是不同性质的指标）。"""
    cases: list[CaseResult] = []
    regressions = _load_yaml(_EDGE_REGRESSIONS)
    hijack_guard = regressions.get("hijack_guard", [])
    for c in hijack_guard:
        text = c["text"]
        forbid = c["forbid_object"]
        structured = classify_structured(text)
        actual = structured["data"].get("object") if structured else None
        cases.append(CaseResult(
            id=f"object_recognition_guardrail::{text}", bucket="object_recognition_guardrail",
            text=text, expected=f"!= {forbid}", actual=actual, passed=(actual != forbid),
            source=c.get("source", ""), tags=c.get("tags", []),
        ))
    sources = [{"path": f"{_EDGE_REGRESSIONS.relative_to(_ROOT).as_posix()}::hijack_guard", "count": len(hijack_guard)}]
    return cases, sources


def _multi_intent_cases() -> tuple[list[CaseResult], list[dict]]:
    """multi_intent_split / multi_intent_no_split 两桶：该拆的拆了 / 不该拆的没拆。"""
    cases: list[CaseResult] = []
    multi_intent = _load_yaml(_MULTI_INTENT)

    split_cases = multi_intent["split"]
    for c in split_cases:
        text = c["text"]
        expected_parts = c["parts"]
        result = split_and_classify_any(text)
        actual_parts = len(result) if result is not None else 0
        cases.append(CaseResult(
            id=f"multi_intent_split::{text}", bucket="multi_intent_split", text=text,
            expected=expected_parts, actual=actual_parts, passed=(actual_parts == expected_parts),
            source=f"{_MULTI_INTENT.relative_to(_ROOT).as_posix()}::split",
        ))

    no_split_cases = multi_intent["no_split"]
    for c in no_split_cases:
        text = c["text"]
        result = split_and_classify_any(text)
        cases.append(CaseResult(
            id=f"multi_intent_no_split::{text}", bucket="multi_intent_no_split", text=text,
            expected=None, actual=result, passed=(result is None),
            detail=c.get("reason", ""),
            source=f"{_MULTI_INTENT.relative_to(_ROOT).as_posix()}::no_split",
        ))

    sources = [
        {"path": f"{_MULTI_INTENT.relative_to(_ROOT).as_posix()}::split", "count": len(split_cases)},
        {"path": f"{_MULTI_INTENT.relative_to(_ROOT).as_posix()}::no_split", "count": len(no_split_cases)},
    ]
    return cases, sources


def _run_all() -> tuple[list[CaseResult], list[dict]]:
    all_cases: list[CaseResult] = []
    all_sources: list[dict] = []
    for fn in (_object_recognition_cases, _object_recognition_guardrail_cases, _multi_intent_cases):
        cases, sources = fn()
        all_cases.extend(cases)
        all_sources.extend(sources)
    return all_cases, all_sources


# ── R4.1 P2：全量语料覆盖率报告 ──────────────────────────────────────────────

def _load_jsonl(path: Path) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def _coverage_report() -> dict:
    """对全量 jsonl 跑 classify_structured，算总体/分域/分对象识别率（识别=返回非 None）。

    与 gap-analysis §3 同口径：只判「有没有被端侧接住」（非 None），不判分类是否完全正确。
    edge_expected!=false 的子集另算「端侧应接子集覆盖率」（§5.3 甄别回填后才有意义）。
    """
    from eval_common import git_short_sha
    items = _load_jsonl(_FULL_CORPUS)

    def _agg():
        return {"count": 0, "recognized": 0}

    total, edge_subset = _agg(), _agg()
    by_domain: dict[str, dict] = {}
    by_object: dict[str, dict] = {}
    for it in items:
        recognized = classify_structured(it["text"]) is not None
        for bucket_map, key in ((by_domain, it.get("domain") or "(空)"),
                                (by_object, it.get("object") or "(空)")):
            b = bucket_map.setdefault(key, _agg())
            b["count"] += 1
            b["recognized"] += recognized
        total["count"] += 1
        total["recognized"] += recognized
        if it.get("edge_expected") is not False:   # true / null 计入「端侧应接子集」（false 剔除）
            edge_subset["count"] += 1
            edge_subset["recognized"] += recognized

    def _rate(b):
        b["rate"] = round(b["recognized"] / b["count"], 4) if b["count"] else 0.0
        return b

    _rate(total)
    _rate(edge_subset)
    for b in by_domain.values():
        _rate(b)
    for b in by_object.values():
        _rate(b)
    return {
        "meta": {"subject": "fast_intent_coverage",
                 "generated_at": datetime.now(timezone.utc).isoformat(),
                 "commit": git_short_sha(),
                 "corpus": _FULL_CORPUS.relative_to(_ROOT).as_posix()},
        "total": total,
        "edge_expected_subset": edge_subset,
        "by_domain": dict(sorted(by_domain.items(), key=lambda kv: -kv[1]["count"])),
        "by_object": dict(sorted(by_object.items(), key=lambda kv: -kv[1]["count"])),
    }


def _render_coverage_md(report: dict) -> str:
    t = report["total"]
    es = report["edge_expected_subset"]
    lines = [
        "# fast_intent 覆盖率报告（全量飞书语料）",
        "",
        f"生成时间：{report['meta']['generated_at']}　commit：{report['meta']['commit'] or '(unknown)'}",
        f"语料：{report['meta']['corpus']}",
        "",
        f"**总体识别率：{t['rate'] * 100:.1f}%**（{t['recognized']}/{t['count']}）",
        f"端侧应接子集（edge_expected!=false）：{es['rate'] * 100:.1f}%（{es['recognized']}/{es['count']}）",
        "",
        "## 分域",
        "| domain | 条数 | 识别率 |",
        "|---|---|---|",
    ]
    for d, b in report["by_domain"].items():
        lines.append(f"| {d} | {b['count']} | {b['rate'] * 100:.1f}% |")
    lines += ["", "## 分对象（前 40，按条数降序）", "| object | 条数 | 识别率 |", "|---|---|---|"]
    for o, b in list(report["by_object"].items())[:40]:
        lines.append(f"| {o} | {b['count']} | {b['rate'] * 100:.1f}% |")
    lines.append("")
    return "\n".join(lines)


def _run_coverage(args) -> int:
    report = _coverage_report()
    md = _render_coverage_md(report)
    t = report["total"]
    print(f"总体识别率 {t['rate'] * 100:.2f}%（{t['recognized']}/{t['count']}）")

    if args.write_baseline:
        write_report(report, md, _COVERAGE_JSON, _COVERAGE_JSON.with_suffix(".md"))
        print(f"覆盖率快照已写入 {_COVERAGE_JSON}")
        return 0
    if args.out_json and args.out_md:
        write_report(report, md, Path(args.out_json), Path(args.out_md))

    prev = load_baseline(_COVERAGE_JSON)
    if prev is None:
        print(f"[提示] 未找到覆盖率快照 {_COVERAGE_JSON}（先 --corpus full --write-baseline）")
        return 0
    delta = (report["total"]["rate"] - prev["total"]["rate"]) * 100
    print(f"较快照变化：{delta:+.2f}pt（快照 {prev['total']['rate'] * 100:.2f}%）")
    if delta < -1.0:
        print(f"::warning::eval_fast_intent 覆盖率较快照下降 {-delta:.2f}pt（>1pt）")
    return 1 if (delta < -1.0 and args.strict) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="T3.4 端侧 fast_intent 意图路由评测")
    ap.add_argument("--baseline", default=str(_DEFAULT_BASELINE))
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--out-json")
    ap.add_argument("--out-md")
    ap.add_argument("--strict", action="store_true",
                     help="有回归时 exit 1；默认不启用，CI 走非阻塞观测")
    ap.add_argument("--corpus", choices=["curated", "full"], default="curated",
                     help="curated=策展基线（默认，逐例回归）；full=全量飞书语料覆盖率报告（R4.1 P2）")
    args = ap.parse_args()

    if args.corpus == "full":
        return _run_coverage(args)

    cases, sources = _run_all()
    report = build_report("fast_intent", sources, cases)
    md = render_markdown(report)

    for c in cases:
        status = "PASS" if c.passed else "FAIL"
        print(f"  {status}  [{c.bucket}] {c.text!r}")
    overall = report["overall"]
    print(f"\n总计 {overall['passed']}/{overall['total']} passed")

    baseline_path = Path(args.baseline)
    if args.write_baseline:
        write_report(report, md, baseline_path, baseline_path.with_suffix(".md"))
        print(f"基线已写入 {baseline_path}")
        return 0

    if args.out_json and args.out_md:
        write_report(report, md, Path(args.out_json), Path(args.out_md))

    baseline = load_baseline(baseline_path)
    if baseline is None:
        print(f"[提示] 未找到基线 {baseline_path}，跳过比对（先 --write-baseline）")
        return 0

    diff = diff_against_baseline(report, baseline)
    print_ci_annotations("eval_fast_intent", diff, baseline_path)
    return 1 if (diff.has_regressions and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
