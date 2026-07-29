"""范例库工具链（M5 P1 数据飞轮）：把三个来源的数据变成 `skills/exemplars/*.yaml`。

设计 `docs/design/2026-07-28-intent-accuracy-data-flywheel.md` §5-P1；契约
`skills/exemplars/README.md`。

  python scripts/exemplars.py import-manifests [--apply]   # 来源②：manifest examples 盘活
  python scripts/exemplars.py from-labels [--since ...] [--apply]  # 来源①：badcase 标注转化
  python scripts/exemplars.py stats                        # 语料体检（域/来源/条数分布）

**治理⑤不变**：默认 dry-run 只打印 diff，`--apply` 才落盘；落盘也只是**改工作区文件**，
入库仍走人审 + git（脚本不碰 git）。来源③（evolve 第四类提案）在 `scripts/evolve.py`
里产草案，不在这里。

**只追加不插入**：eid 是 `<domain>#<序号>`，插队会让昨天日报里的 eid 指向别的条目。
所有写入路径都是「读现有 → 去重 → append → 写回」。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
_EX_DIR = _ROOT / "skills" / "exemplars"
_TZ = timezone(timedelta(hours=8))


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def _domain_of(intent: str) -> str:
    return str(intent or "").split(".")[0].strip()


def _load_file(domain: str) -> dict:
    p = _EX_DIR / f"{domain}.yaml"
    if not p.is_file():
        return {"domain": domain, "exemplars": []}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SystemExit(f"✗ {p} 顶层不是映射，先手工修")
    raw.setdefault("domain", domain)
    raw.setdefault("exemplars", [])
    return raw


def _dump_file(domain: str, doc: dict) -> str:
    head = (f"# 范例库 · {domain} 域（M5 P1 数据飞轮）\n"
            f"# 契约 skills/exemplars/README.md；只追加不插入（eid=<domain>#序号，"
            f"插队会让历史日报的 eid 指错条目）。\n")
    body = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120)
    return head + body


def _write(domain: str, doc: dict, apply: bool) -> None:
    _EX_DIR.mkdir(parents=True, exist_ok=True)
    text = _dump_file(domain, doc)
    p = _EX_DIR / f"{domain}.yaml"
    if not apply:
        print(f"  [dry-run] 将写 {p.relative_to(_ROOT)}（{len(doc['exemplars'])} 条）")
        return
    p.write_text(text, encoding="utf-8")
    print(f"  ✓ {p.relative_to(_ROOT)}（{len(doc['exemplars'])} 条）")


def _existing_texts(doc: dict) -> set[str]:
    return {str(r.get("text") or "").strip()
            for r in doc.get("exemplars") or [] if isinstance(r, dict)}


# ── 来源②：manifest examples 批量导入 ────────────────────────────────────────

def cmd_import_manifests(args) -> int:
    """agents/*/manifest.yaml 的 `examples` → 范例（source=manifest，只带 intent 无槽）。

    这 199 条例句此前是**死资产**：不进 planner prompt（`context.py::_catalog_item` 只渲染
    intent/slots/desc），只喂 registry 的逐字符打分（RFC 断点④-③）。导入即盘活。

    **跨 agent 同句必须丢弃**：同一句话被两个 agent 都写成 examples，本身就是路由歧义，
    当成范例注入等于教模型「这句话两个域都对」。丢弃并打印，让人去修 manifest。"""
    by_text: dict[str, list[tuple[str, str]]] = {}     # text → [(agent_id, intent)]
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        agent_id = str(m.get("agent_id") or "").strip()
        for c in m.get("capabilities") or []:
            intent = str(c.get("intent") or "").strip()
            for ex in c.get("examples") or []:
                t = str(ex or "").strip()
                if t and intent:
                    by_text.setdefault(t, []).append((agent_id, intent))

    ambiguous = {t: v for t, v in by_text.items() if len({i for _, i in v}) > 1}
    for t, v in sorted(ambiguous.items()):
        print(f"  ⚠ 跨能力同句，丢弃（先修 manifest）：{t!r} → {[i for _, i in v]}")

    staged: dict[str, list[dict]] = {}
    for t, v in by_text.items():
        if t in ambiguous:
            continue
        agent_id, intent = v[0]
        domain = _domain_of(intent)
        if not domain:
            continue
        staged.setdefault(domain, []).append(
            {"text": t, "plan": [{"agent": agent_id, "intent": intent}],
             "source": "manifest", "added": _today(), "tags": ["canonical"]})

    total_new = 0
    for domain in sorted(staged):
        doc = _load_file(domain)
        have = _existing_texts(doc)
        fresh = [r for r in sorted(staged[domain], key=lambda r: r["text"])
                 if r["text"] not in have]
        if not fresh:
            continue
        doc["exemplars"] = list(doc.get("exemplars") or []) + fresh
        total_new += len(fresh)
        print(f"{domain}: +{len(fresh)} 条")
        _write(domain, doc, args.apply)
    print(f"\n合计新增 {total_new} 条（跨能力歧义丢弃 {len(ambiguous)} 句）"
          + ("" if args.apply else "；dry-run，加 --apply 落盘"))
    return 0


# ── 来源①：badcase 标注转化 ─────────────────────────────────────────────────

def cmd_from_labels(args) -> int:
    """collector `/api/export/labels`（P0 落地的标注载体）→ 范例草案（source=trace）。

    一次标注生成两种资产（RFC §5 飞轮图）：这里出**检索范例**；同一份导出同时是
    RoutingBench 的评测用例来源（P2 的 runner 直读同一个 endpoint，不在这里重复造）。

    gold_intents 是标注人给的**正确落域**；agent 由 intent 的域反查 manifest 填，
    查不到就留空——范例块里 agent 可省，intent 才是落域信号。"""
    base = (args.collector or "http://localhost:8092").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/export/labels", timeout=15) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"✗ collector 不可达（{base}）：{e}")
        return 1
    # 导出体是 {exported_at, count, labels:[...]} 而不是裸数组（server.py::export_labels）。
    # 容两种形状——但真形状以 collector 为准，这里对齐的是它。
    rows = payload.get("labels", []) if isinstance(payload, dict) else payload
    intent_agent = _intent_to_agent()
    staged: dict[str, list[dict]] = {}
    skipped = 0
    for row in rows:
        text = str(row.get("user_text") or "").strip()
        gold = [g.strip() for g in str(row.get("gold_intents") or "").split(",") if g.strip()]
        if not text or not gold:
            skipped += 1
            continue
        if args.since and str(row.get("ts") or "") and _ts_day(row["ts"]) < args.since:
            continue
        unknown = [g for g in gold if g not in intent_agent]
        if unknown:
            print(f"  ⚠ 跳过：标注意图不存在于任何 manifest {unknown} ←「{text}」")
            skipped += 1
            continue
        domain = _domain_of(gold[0])
        staged.setdefault(domain, []).append(
            {"text": text,
             "plan": [{"agent": intent_agent[g], "intent": g} for g in gold],
             "source": "trace", "added": _today(),
             "tags": ["badcase"], "note": str(row.get("note") or "")[:80] or None})

    total = 0
    for domain in sorted(staged):
        doc = _load_file(domain)
        have = _existing_texts(doc)
        fresh = [{k: v for k, v in r.items() if v is not None}
                 for r in staged[domain] if r["text"] not in have]
        if not fresh:
            continue
        doc["exemplars"] = list(doc.get("exemplars") or []) + fresh
        total += len(fresh)
        print(f"{domain}: +{len(fresh)} 条")
        _write(domain, doc, args.apply)
    print(f"\n合计新增 {total} 条（跳过 {skipped}）"
          + ("" if args.apply else "；dry-run，加 --apply 落盘"))
    return 0


def _ts_day(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts) / 1000, _TZ).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _intent_to_agent() -> dict[str, str]:
    out = {}
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        aid = str(m.get("agent_id") or "").strip()
        for c in m.get("capabilities") or []:
            if c.get("intent"):
                out.setdefault(str(c["intent"]), aid)
    return out


# ── 体检 ─────────────────────────────────────────────────────────────────────

def cmd_stats(args) -> int:
    files = sorted(_EX_DIR.glob("*.yaml")) if _EX_DIR.is_dir() else []
    if not files:
        print("（skills/exemplars/ 下没有范例文件）")
        return 0
    total, by_source, by_domain = 0, {}, {}
    for p in files:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        rows = [r for r in (raw.get("exemplars") or []) if isinstance(r, dict)]
        domain = str(raw.get("domain") or p.stem)
        by_domain[domain] = len(rows)
        total += len(rows)
        for r in rows:
            s = str(r.get("source") or "manual")
            by_source[s] = by_source.get(s, 0) + 1
    print(f"范例总数 {total}（{len(files)} 域）")
    print("  按域：" + "、".join(f"{k}×{v}" for k, v in
                                 sorted(by_domain.items(), key=lambda kv: -kv[1])))
    print("  按来源：" + "、".join(f"{k}×{v}" for k, v in
                                   sorted(by_source.items(), key=lambda kv: -kv[1])))
    # N2「规则净增速率 ≤0」的另一半：范例净增速率应 >0，否则飞轮没在转
    print("\n提示：source=manifest 是死资产盘活（一次性），source=trace 才是飞轮在转的证据。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("import-manifests", help="manifest examples → 范例（一次性盘活）")
    p1.add_argument("--apply", action="store_true", help="真正落盘（缺省 dry-run）")
    p2 = sub.add_parser("from-labels", help="collector 标注导出 → 范例")
    p2.add_argument("--apply", action="store_true")
    p2.add_argument("--collector", default="", help="默认 http://localhost:8092")
    p2.add_argument("--since", default="", help="只取该日期（YYYY-MM-DD）及之后的标注")
    sub.add_parser("stats", help="语料体检")
    args = ap.parse_args()
    return {"import-manifests": cmd_import_manifests,
            "from-labels": cmd_from_labels,
            "stats": cmd_stats}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
