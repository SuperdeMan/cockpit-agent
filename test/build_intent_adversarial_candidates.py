#!/usr/bin/env python
"""从现有资产生成**只读** candidate 审阅队列。

三条硬规矩：

1. **只产 candidate**：provenance 里绝不写 `reviewed_by: human`。机器导入的东西自称
   人审过，整个生命周期就白建了。
2. **不写进已提交语料目录**：输出只允许落 ignored 的 review queue。
3. **不猜 gold**：映射不出精确 intent、含否定/多意图/上下文依赖的行进 `manual_review`。

用法：
  python test/build_intent_adversarial_candidates.py --out docs/reviews/eval/_ci-run-intent-candidates.yaml
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
for _p in (str(ROOT), str(ROOT / "gen" / "python"), str(ROOT / "orchestrator" / "edge"),
           str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROTECTED_DIR = "eval_corpus/intent_adversarial"

# 需要人裁的语义标记：否定/条件/指代/多意图——这些行的 gold 不是「一个 intent」。
_MANUAL_MARKERS = ("不要", "别", "取消", "如果", "刚才", "上一个", "那个", "再",
                   "顺便", "然后", "并且", "以及", "同时")

# 脱敏：姓名难以正则识别，交给人工复核；能机械识别的先拦。
_PII_PATTERNS = (
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("plate", re.compile(r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤"
                         r"青藏川宁琼使领][A-Z][A-Z0-9]{5}")),
    ("bank_card", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("token", re.compile(r"(?i)\b(?:sk|pat|ghp|xoxb|bearer)[-_ ]?[A-Za-z0-9]{16,}")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("address", re.compile(r"[省市区县][一-龥]{0,8}(?:路|街|道|号楼|小区)\d*号?")),
)


def privacy_violations(text: str) -> list[str]:
    return [name for name, pattern in _PII_PATTERNS if pattern.search(text or "")]


def _normalise(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?~～]+", "", str(text or "")).lower()


def _candidate(text: str, intents: list[str], *, family: str, kind: str,
               source_ref: str, source_key: str = "",
               context: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": f"candidate.{kind}.{_normalise(text)[:24]}",
        "family_id": family,
        "status": "candidate",
        "input": {"utterance": text, "context": dict(context or {})},
        "required_intent_groups": [{"any_of": list(intents)}],
        "provenance": {"kind": kind, "source_ref": source_ref,
                       **({"source_key": source_key} if source_key else {})},
    }


# ── 来源 1：manifest capability examples ───────────────────────────────────


def import_manifest_examples(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rows = []
    for cap in data.get("capabilities") or []:
        intent = str(cap.get("intent") or "")
        if not intent:
            continue
        for example in cap.get("examples") or []:
            rows.append(_candidate(str(example), [intent],
                                   family=f"asset.manifest.{intent}",
                                   kind="manifest_example",
                                   source_ref=str(path), source_key=intent))
    return rows


# ── 来源 2/3：真实 Skill / Exemplar store ─────────────────────────────────


def import_skill_golden() -> list[dict[str, Any]]:
    from orchestrator.cloud import skills as _skills
    rows = []
    for doc in _skills.default_store().load(force=True):
        for item in (doc.golden or ()):
            text = str((item or {}).get("user") or "")
            intents = [str(i) for i in ((item or {}).get("expect_any") or [])
                       if "." in str(i)]
            if not text or not intents:
                continue
            rows.append(_candidate(text, intents, family=f"asset.skill.{doc.name}",
                                   kind="skill_golden", source_ref=doc.path or doc.name,
                                   source_key=doc.name))
    return rows


def import_exemplars() -> list[dict[str, Any]]:
    from orchestrator.cloud import exemplars as _exemplars
    rows = []
    for item in _exemplars.default_store().load(force=True):
        intents = [str(step.get("intent")) for step in (item.plan or [])
                   if isinstance(step, dict) and step.get("intent")]
        if not intents:
            continue
        rows.append(_candidate(item.text, intents,
                               family=f"asset.exemplar.{item.domain}",
                               kind="exemplar", source_ref="skills/exemplars",
                               source_key=item.eid))
    return rows


# ── 来源 4：现有 eval 语料 ────────────────────────────────────────────────

_CORPUS_FILES = ("mode_routing_cases.yaml", "clarify_cases.yaml",
                 "rejection_cases.yaml", "route_hints_cases.yaml",
                 "edge_regressions.yaml")


def import_eval_corpus(directory: Path) -> list[dict[str, Any]]:
    rows = []
    for name in _CORPUS_FILES:
        path = Path(directory) / name
        if not path.is_file():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = data.get("cases") if isinstance(data, dict) else data
        for index, case in enumerate(entries or []):
            if not isinstance(case, dict):
                continue
            text = str(case.get("text") or case.get("utterance") or "")
            intents = [str(i) for i in (case.get("expect_intents")
                                        or case.get("expect_final_intents")
                                        or ([case["expect_intent"]]
                                            if case.get("expect_intent") else []))
                       if "." in str(i)]
            if not text or not intents:
                continue
            rows.append(_candidate(text, intents, family=f"asset.{name}.{index}",
                                   kind="eval_corpus", source_ref=str(path),
                                   source_key=f"{name}#{index}"))
    return rows


# ── 来源 5：collector 人工标注（显式开启，只读） ─────────────────────────


def import_collector_labels(base_url: str, timeout: float = 15.0
                            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """只 GET `<base>/api/export/labels`；不写标注、不读 SQLite。"""
    url = f"{base_url.rstrip('/')}/api/export/labels"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:                       # 拿不到就是拿不到，不猜
        return [], [{"reason": "collector_unreachable", "detail": str(exc)}]
    rows, rejected = [], []
    for turn in (payload.get("turns") or []):
        text = str(turn.get("user_text") or "")
        intents = [str(i) for i in (turn.get("gold_intents") or []) if "." in str(i)]
        if not text or not intents:
            continue
        hits = privacy_violations(text)
        if hits:
            rejected.append({"reason": "privacy_rejected", "kinds": hits,
                             "trace_id": str(turn.get("trace_id") or "")})
            continue
        rows.append(_candidate(text, intents, family="collector",
                               kind="collector_label", source_ref=url,
                               source_key=str(turn.get("trace_id") or "")))
    return rows, rejected


# ── 冲突 / 去重 / 归组 ────────────────────────────────────────────────────


def deduplicate_candidates(rows: list[dict[str, Any]]
                           ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """同文本 + 同上下文 + 同 gold 合并来源；gold 不同进 conflicts（不投票、不取并集）。"""
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (_normalise(row["input"]["utterance"]),
               json.dumps(row["input"].get("context") or {}, sort_keys=True,
                          ensure_ascii=False))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(row)
    accepted, conflicts = [], []
    for key in order:
        group = buckets[key]
        golds = {json.dumps(row["required_intent_groups"], sort_keys=True,
                            ensure_ascii=False) for row in group}
        if len(golds) > 1:
            conflicts.append({
                "reason": "conflicting_gold",
                "utterance": group[0]["input"]["utterance"],
                "golds": sorted(golds),
                "sources": [row["provenance"].get("source_ref", "") for row in group],
            })
            continue
        merged = dict(group[0])
        merged["provenance"] = dict(group[0]["provenance"])
        merged["provenance"]["merged_sources"] = sorted(
            {row["provenance"].get("source_ref", "") for row in group})
        accepted.append(merged)
    return accepted, conflicts


def split_manual_review(rows: list[dict[str, Any]]
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    auto, manual = [], []
    for row in rows:
        text = row["input"]["utterance"]
        if len(row["required_intent_groups"][0]["any_of"]) > 1:
            manual.append({**row, "manual_reason": "multi_intent_gold"})
        elif any(marker in text for marker in _MANUAL_MARKERS):
            manual.append({**row, "manual_reason": "negation_or_reference"})
        elif privacy_violations(text):
            manual.append({**row, "manual_reason": "privacy_review"})
        else:
            auto.append(row)
    return auto, manual


def write_candidates(rows: list[dict[str, Any]], out_path: Path) -> Path:
    """只允许写 ignored review queue。写进已提交语料目录 = 机器自己给自己发 gold。"""
    path = Path(out_path)
    if PROTECTED_DIR in path.as_posix():
        raise ValueError(
            f"refusing to write into the committed corpus; use a review queue path "
            f"outside {PROTECTED_DIR}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"schema_version": 1, "candidates": rows},
                                   allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path


def collect_all(collector_url: str = "") -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for manifest in sorted((ROOT / "agents").glob("*/manifest.yaml")):
        rows.extend(import_manifest_examples(manifest))
    rows.extend(import_skill_golden())
    rows.extend(import_exemplars())
    rows.extend(import_eval_corpus(ROOT / "test" / "eval_corpus"))
    rejected: list[dict[str, Any]] = []
    if collector_url:
        collector_rows, rejected = import_collector_labels(collector_url)
        rows.extend(collector_rows)
    raw_count = len(rows)
    accepted, conflicts = deduplicate_candidates(rows)
    auto, manual = split_manual_review(accepted)
    return {"accepted": auto, "manual_review": manual, "conflicts": conflicts,
            "privacy_rejected": rejected,
            "duplicates": raw_count - len(accepted) - len(conflicts)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--collector-url", default="")
    args = ap.parse_args(argv)
    bundle = collect_all(args.collector_url)
    write_candidates(bundle["accepted"] + bundle["manual_review"], Path(args.out))
    queue = Path(args.out)
    write_candidates(bundle["conflicts"],
                     queue.with_name(queue.stem + "-conflicts" + queue.suffix))
    print(f"accepted={len(bundle['accepted'])} "
          f"manual_review={len(bundle['manual_review'])} "
          f"conflicts={len(bundle['conflicts'])} "
          f"duplicates={bundle['duplicates']} "
          f"privacy_rejected={len(bundle['privacy_rejected'])}")
    print(f"review queue → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
