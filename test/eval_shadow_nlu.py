"""M1b Cloud Shadow NLU 影子评测（RFC docs/design/2026-07-24-m1b-self-evolution-shadow-nlu-rfc.md §2）。

对 8590 行全量语料跑「fast_intent 规则臂 vs LLM @fast 档臂」对照，产 domain 级混淆
矩阵 + 三桶决策表 + 切换建议清单——Edge Semantic NLU（真 T0.5，M2+）的决策输入。
**纯离线影子：不动 fast_intent、不接运行时、不碰线上请求。**

口径（RFC §2.1）：
  - 规则臂 hit = classify_structured(text) is not None（与 eval_fast_intent --corpus full
    / gap-analysis 同口径，可与 72% 覆盖率数字直比）；规则 domain 一致性仅作次指标。
  - LLM 臂 = domain 封闭集分类准确率（金标 = 语料 domain 字段）；槽位照产不算准确率
    （无金标），分域采样进报告供人工抽看。
  - 三桶：rule_hit / rule_miss & llm_ok（切换候选）/ rule_miss & llm_bad（语料或域定义问题）。

用法：
  python test/eval_shadow_nlu.py --sample 500 --provider minimax   # 校准跑
  python test/eval_shadow_nlu.py --provider minimax                # 全量（断点续跑）
  python test/eval_shadow_nlu.py --report-only                     # 只从已有结果出报告
断点：结果增量写 docs/reviews/eval/shadow_nlu_results.jsonl（按语料行号 idx 幂等），
中断重跑自动跳过已完成条目；解析失败条目不落盘、重跑自动重试。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
for p in (str(_ROOT), str(_ROOT / "gen" / "python"),
          os.path.join(str(_ROOT), "orchestrator", "edge"), str(Path(__file__).parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval_common import ProviderLock, git_short_sha  # noqa: E402
from fast_intent import classify_structured  # noqa: E402

_CORPUS = _ROOT / "test" / "eval_corpus" / "feishu_intents_full.jsonl"
_RESULTS = _ROOT / "docs" / "reviews" / "eval" / "shadow_nlu_results.jsonl"
_REPORT_JSON = _ROOT / "docs" / "reviews" / "eval" / "shadow_nlu_report.json"
_REPORT_MD = _ROOT / "docs" / "reviews" / "eval" / "shadow_nlu_report.md"

# 语料 domain 封闭集（9 域，含义按语料样本口径）；LLM 只许从中选或 unknown。
_DOMAINS = {
    "setting": "车辆设置/车控硬件操作（座椅/空调/车窗/灯光/驾驶辅助设置等）",
    "weather": "天气查询",
    "media": "音乐/电台/视频/音量播控",
    "navi": "导航/位置/路线/地点搜索",
    "information": "信息查询（资讯/百科/股票/赛事/知识问答）",
    "phone": "电话/通讯录/通话",
    "app": "打开或操作车机应用",
    "base": "基础对话（问候/闲聊/助手自身）",
}

_SYSTEM = (
    "你是车载语音意图分类器。对下面每句话输出所属领域和关键槽位。\n"
    "领域封闭集（domain 只能取以下值，确实判不了输出 \"unknown\"）：\n"
    + "\n".join(f"- {k}: {v}" for k, v in _DOMAINS.items())
    + "\n\n输出严格 JSON 数组，与输入序号一一对应，不要任何解释：\n"
    '[{"id":1,"domain":"setting","slots":{"object":"座椅加热","action":"打开"}}]\n'
    "slots 键值只从话术里抽取（object/action/city/date/keyword 等），话术里没有就给空对象 {}。"
)


def _load_corpus() -> list[dict]:
    items = []
    with open(_CORPUS, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if line:
                it = json.loads(line)
                it["idx"] = i
                items.append(it)
    return items


def _load_done() -> dict[int, dict]:
    done: dict[int, dict] = {}
    if _RESULTS.exists():
        with open(_RESULTS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    done[int(r["idx"])] = r
    return done


def _extract_json_array(s: str) -> str:
    i, j = s.find("["), s.rfind("]")
    return s[i:j + 1] if i >= 0 and j > i else s


def parse_batch_reply(raw: str, batch: list[dict]) -> list[dict]:
    """解析一批 LLM 回复：id 对位、domain 白名单校验、slots 截断。
    解析失败/缺位的条目不返回（不落盘 → 重跑自动重试，RFC §2.2）。"""
    try:
        arr = json.loads(_extract_json_array(raw))
    except (ValueError, TypeError):
        return []
    if not isinstance(arr, list):
        return []
    by_id = {}
    for o in arr:
        if isinstance(o, dict) and isinstance(o.get("id"), int):
            by_id[o["id"]] = o
    out = []
    for pos, it in enumerate(batch, 1):
        o = by_id.get(pos)
        if o is None:
            continue
        dom = str(o.get("domain") or "").strip().lower()
        if dom not in _DOMAINS and dom != "unknown":
            dom = "unknown"          # 白名单外（LLM 发明域名）归 unknown，不发明新桶
        slots = o.get("slots") if isinstance(o.get("slots"), dict) else {}
        slots = {str(k)[:40]: str(v)[:80] for k, v in list(slots.items())[:8]}
        out.append({"idx": it["idx"], "llm_domain": dom, "llm_slots": slots})
    return out


def _make_llm():
    import grpc
    from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
    addr = os.getenv("LLM_GATEWAY_ADDR", "localhost:50052")
    stub = llm_pb2_grpc.LLMGatewayStub(grpc.insecure_channel(addr))

    def _call(batch: list[dict], provider: str) -> str:
        lines = "\n".join(f"{i}. {it['text']}" for i, it in enumerate(batch, 1))
        req = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role="system", content=_SYSTEM),
                      llm_pb2.Message(role="user", content=lines)],
            model="@fast", temperature=0.1, max_tokens=1200)
        req.meta["thinking"] = "off"
        req.meta["caller_service"] = "eval-shadow-nlu"
        if provider:
            req.meta["llm_provider"] = provider
        return stub.Complete(req, timeout=60).content

    return _call


async def _run_llm_arm(items: list[dict], provider: str, batch_size: int,
                       concurrency: int) -> None:
    done = _load_done()
    todo = [it for it in items if it["idx"] not in done]
    if not todo:
        print(f"LLM 臂：{len(done)} 条已全部完成（断点文件 {_RESULTS.name}）")
        return
    print(f"LLM 臂：待跑 {len(todo)} 条（已完成 {len(done)}），"
          f"批 {batch_size}×并发 {concurrency}")
    call = _make_llm()
    sem = asyncio.Semaphore(concurrency)
    _RESULTS.parent.mkdir(parents=True, exist_ok=True)
    lock = asyncio.Lock()
    stats = {"ok": 0, "fail": 0}

    async def one_batch(batch: list[dict], bi: int, total: int):
        async with sem:
            try:
                raw = await asyncio.to_thread(call, batch, provider)
                rows = parse_batch_reply(raw, batch)
            except Exception as e:
                print(f"  [{bi}/{total}] 批失败：{type(e).__name__}: {str(e)[:120]}")
                stats["fail"] += len(batch)
                return
            async with lock:
                with open(_RESULTS, "a", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
            stats["ok"] += len(rows)
            stats["fail"] += len(batch) - len(rows)
            if bi % 20 == 0 or bi == total:
                print(f"  [{bi}/{total}] 累计 ok={stats['ok']} 待重试={stats['fail']}")

    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    await asyncio.gather(*(one_batch(b, i + 1, len(batches))
                           for i, b in enumerate(batches)))
    print(f"LLM 臂完成：新增 ok={stats['ok']}，解析失败待重试={stats['fail']}"
          f"（重跑本命令自动补）")


def _build_report(items: list[dict], sample_idx: set[int] | None) -> dict:
    done = _load_done()
    scope = [it for it in items
             if (sample_idx is None or it["idx"] in sample_idx)]
    rows = []
    for it in scope:
        gold = (it.get("domain") or "").strip() or None
        r = classify_structured(it["text"])
        llm = done.get(it["idx"])
        rows.append({
            "idx": it["idx"], "text": it["text"], "gold": gold,
            "edge_sub": it.get("edge_expected") is not False,
            "rule_hit": r is not None,
            "rule_domain": (r or {}).get("domain", ""),
            "llm_domain": (llm or {}).get("llm_domain"),
            "llm_slots": (llm or {}).get("llm_slots") or {},
        })
    covered = [r for r in rows if r["llm_domain"] is not None and r["gold"]]

    def _rate(n, d):
        return round(n / d, 4) if d else 0.0

    total_rule = _rate(sum(r["rule_hit"] for r in rows), len(rows))
    llm_acc = _rate(sum(r["llm_domain"] == r["gold"] for r in covered), len(covered))
    edge_rows = [r for r in rows if r["edge_sub"]]
    edge_cov = [r for r in covered if r["edge_sub"]]

    confusion: dict[str, dict[str, int]] = {}
    buckets: dict[str, dict] = {}
    samples: dict[str, list] = {}
    for r in covered:
        confusion.setdefault(r["gold"], {})
        confusion[r["gold"]][r["llm_domain"]] = confusion[r["gold"]].get(r["llm_domain"], 0) + 1
        b = buckets.setdefault(r["gold"], {"count": 0, "rule_hit": 0,
                                           "switch_candidate": 0, "both_miss": 0})
        b["count"] += 1
        if r["rule_hit"]:
            b["rule_hit"] += 1
        elif r["llm_domain"] == r["gold"]:
            b["switch_candidate"] += 1
        else:
            b["both_miss"] += 1
        if r["llm_slots"] and len(samples.setdefault(r["gold"], [])) < 3:
            samples[r["gold"]].append({"text": r["text"], "slots": r["llm_slots"]})
    for d, b in buckets.items():
        b["rule_hit_rate"] = _rate(b["rule_hit"], b["count"])
        b["switch_gain"] = _rate(b["switch_candidate"], b["count"])

    switch = sorted(
        ({"domain": d, **b} for d, b in buckets.items()),
        key=lambda x: -(x["switch_gain"] * x["count"]))
    return {
        "meta": {"subject": "shadow_nlu", "generated_at":
                 datetime.now(timezone.utc).isoformat(), "commit": git_short_sha(),
                 "corpus": _CORPUS.relative_to(_ROOT).as_posix(),
                 "scope": len(rows), "llm_covered": len(covered)},
        "rule_arm": {"hit_rate_total": total_rule,
                     "hit_rate_edge_subset": _rate(
                         sum(r["rule_hit"] for r in edge_rows), len(edge_rows))},
        "llm_arm": {"domain_acc_total": llm_acc,
                    "domain_acc_edge_subset": _rate(
                        sum(r["llm_domain"] == r["gold"] for r in edge_cov), len(edge_cov)),
                    "unknown_rate": _rate(
                        sum(r["llm_domain"] == "unknown" for r in covered), len(covered))},
        "confusion": {k: dict(sorted(v.items(), key=lambda kv: -kv[1]))
                      for k, v in sorted(confusion.items())},
        "buckets": {d: b for d, b in sorted(buckets.items(), key=lambda kv: -kv[1]["count"])},
        "switch_ranking": switch,
        "slot_samples": samples,
    }


def _render_md(rep: dict) -> str:
    m, ra, la = rep["meta"], rep["rule_arm"], rep["llm_arm"]
    lines = [
        "# Cloud Shadow NLU 影子评测报告（M1b）", "",
        f"> 生成：{m['generated_at']}　commit：{m['commit']}　语料：{m['corpus']}",
        f"> 范围 {m['scope']} 条，其中 LLM 臂已覆盖 {m['llm_covered']} 条",
        f"> 本报告为离线影子评测——不改变任何运行时行为（RFC §2.3）", "",
        "## 总表", "",
        "| 臂 | 全量 | 端侧应接子集 |", "|---|---|---|",
        f"| 规则 hit 率（coverage 口径） | {ra['hit_rate_total']*100:.1f}% | {ra['hit_rate_edge_subset']*100:.1f}% |",
        f"| LLM domain 准确率 | {la['domain_acc_total']*100:.1f}% | {la['domain_acc_edge_subset']*100:.1f}% |",
        f"", f"LLM unknown 率：{la['unknown_rate']*100:.1f}%", "",
        "## 三桶决策表（分域）", "",
        "| domain | 条数 | 规则 hit | 切换候选（规则✗LLM✓） | 双失 |",
        "|---|---|---|---|---|",
    ]
    for d, b in rep["buckets"].items():
        lines.append(f"| {d} | {b['count']} | {b['rule_hit_rate']*100:.1f}% "
                     f"| {b['switch_gain']*100:.1f}% | {b['both_miss']} |")
    lines += ["", "## 切换建议（按 净增益×流量 排序）", ""]
    for s in rep["switch_ranking"][:5]:
        lines.append(f"1. **{s['domain']}**：规则 hit {s['rule_hit_rate']*100:.1f}%，"
                     f"LLM 净增 {s['switch_gain']*100:.1f}%（{s['switch_candidate']}/{s['count']} 条）")
    lines += ["", "## 混淆矩阵（金标 × LLM）", ""]
    cols = sorted({c for row in rep["confusion"].values() for c in row} | set(rep["confusion"]))
    lines.append("| gold \\ llm | " + " | ".join(cols) + " |")
    lines.append("|---|" + "|".join(["---"] * len(cols)) + "|")
    for g, row in rep["confusion"].items():
        lines.append(f"| {g} | " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    lines += ["", "## 槽位输出采样（无金标，仅供 Edge NLU schema 设计参考）", ""]
    for d, ss in rep["slot_samples"].items():
        for s in ss:
            lines.append(f"- [{d}] {s['text']} → `{json.dumps(s['slots'], ensure_ascii=False)}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Cloud Shadow NLU 影子评测（M1b）")
    ap.add_argument("--sample", type=int, default=0, help="随机抽样 N 条（seed=42；0=全量）")
    ap.add_argument("--provider", default="", help="请求级 pin 厂商（评测口径锁定）")
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--report-only", action="store_true", help="不跑 LLM，只从已有结果出报告")
    args = ap.parse_args()

    items = [it for it in _load_corpus() if (it.get("domain") or "").strip()]
    sample_idx = None
    if args.sample:
        picked = random.Random(42).sample(items, min(args.sample, len(items)))
        sample_idx = {it["idx"] for it in picked}
        items_run = picked
    else:
        items_run = items

    if not args.report_only:
        host = os.getenv("LLM_GATEWAY_HTTP_HOST", "localhost")
        port = os.getenv("AUDIO_HTTP_PORT", "50059")
        lock = ProviderLock(f"http://{host}:{port}", args.provider)
        try:
            provider = lock.pin()
        except RuntimeError as e:
            print(f"✗ {e}")
            return 1
        print(f"active provider：{provider}")
        asyncio.run(_run_llm_arm(items_run, args.provider, args.batch, args.concurrency))
        lock.check("LLM 臂跑完")
        if lock.summary()["drift_detected"]:
            print("⚠️ 评测中途 provider 漂移，本轮结果作废（结果文件已含混脑数据需人工甄别）")
            return 1

    rep = _build_report(items, sample_idx)
    _REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(_REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    md = _render_md(rep)
    with open(_REPORT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    print(md.split("## 混淆矩阵")[0])
    print(f"报告：{_REPORT_JSON.relative_to(_ROOT)} / {_REPORT_MD.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
