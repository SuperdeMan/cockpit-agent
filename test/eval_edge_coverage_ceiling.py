"""端侧覆盖率天花板测量（M5 P3 开工前的可行性判据）。

**为什么在训模型之前先跑这个**：RFC §5-P3 的 DoD 是「端侧覆盖率 76.2% → 85%」，它隐含一个
假设——缺口是**识别**问题（规则认不出说法，换分类器就能接住）。但 R4.1b 的既有裁决同时
写着「NLU 只解决识别不解决执行——**识别了执行不了的域不切**」。两句话必须先对账：
那 1600+ 条 miss 里，有多少是 **VAL 压根执行不了**的？那部分是任何分类器都拿不到的天花板，
训练前就该知道，而不是训完才发现覆盖率涨不上去。

口径与做法：
  - 分母 = **端侧应接子集**（`edge_expected != false`，与 `eval_fast_intent --corpus full`
    的「端侧应接子集覆盖率」同口径，可直比 80.0%）；
  - miss = `classify_structured(text) is None`；按 corpus object **分层比例抽样**；
  - **LLM 只提议、代码来核验**：模型对每句给出 (object, operate, mode/attr)，随后按
    `orchestrator/edge/knowledge/commands.yaml` 逐项结构校验——object 必须存在、operate 必须
    在该 object 的 operates 里、mode 必须在 modes/attrs 里。核验不过一律记不可执行。
    这样模型**没法靠幻觉抬高天花板**（"权威靠不给输入实现"的同款姿态：判定权不交给被测者）。
  - 天花板 = 现覆盖率 + miss 率 × 可执行比例（**上界**：真分类器还会有分类错误与槽位抽取
    失败，实际必然低于它）。

用法：
  python test/eval_edge_coverage_ceiling.py --sample 150 --provider minimax --model MiniMax-M3
  python test/eval_edge_coverage_ceiling.py --report-only     # 只从已有结果重出报告
断点：结果增量写 `docs/reviews/eval/edge_coverage_ceiling_samples.jsonl`（按语料行号幂等）。
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

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
_KNOWLEDGE = _ROOT / "orchestrator" / "edge" / "knowledge" / "commands.yaml"
_EVAL = _ROOT / "docs" / "reviews" / "eval"
_SAMPLES = _EVAL / "edge_coverage_ceiling_samples.jsonl"
_REPORT_JSON = _EVAL / "edge_coverage_ceiling.json"
_REPORT_MD = _EVAL / "edge_coverage_ceiling.md"


def load_val_objects() -> dict:
    return (yaml.safe_load(_KNOWLEDGE.read_text(encoding="utf-8")) or {}).get("objects") or {}


def render_spec(objs: dict) -> str:
    """VAL 可执行面的紧凑描述（进 prompt）。刻意不写自然语言解释——要判的是
    「这句话能不能被这张表里的某一项执行」，不是「这张表好不好」。"""
    lines = []
    for k, v in sorted(objs.items()):
        parts = [f"{k}: ops={'/'.join(v.get('operates') or [])}"]
        if v.get("attrs"):
            parts.append("attrs=" + "/".join(str(a) for a in v["attrs"]))
        if v.get("modes"):
            parts.append("modes=" + "/".join(str(m) for m in v["modes"]))
        if v.get("positions"):
            parts.append("positions=yes")
        lines.append("; ".join(parts))
    return "\n".join(lines)


_SYSTEM = """你在评估一份车载语音「可执行能力表」的覆盖面。下面是车控抽象层（VAL）**全部**\
可执行对象，每行格式 `object: ops=可用操作; attrs=可设属性; modes=可选模式; positions=yes 表示支持座位/方位`。

{spec}

用户会给你编号的句子。对每一句判断：**这张表里是否存在某一项能真正执行它**。
- 能：输出该 object、operate，以及用到的 mode 或 attr（都必须逐字来自上表，不得发明）。
- 不能：object 填 NONE。

**判据是「同一个东西」，不是「最接近的东西」——宁缺毋滥。** 只有当表里的 object/mode 与用户
说的**指同一个车辆功能**时才算能执行；只要是**另一个功能**，哪怕名字像、类别同、听起来能凑，
一律 NONE。三个真实的反面例子（这些都必须判 NONE）：
- 「打开速度控制模式」→ 不是 driving_mode 的 sport/运动（那是动力风格），是定速巡航，表里没有；
- 「主动变道模式关闭」→ 不是 lane_assistance（车道保持），是自动变道，表里没有；
- 「屏保打开」→ 不是 screen 的开关或亮度，是屏保，表里没有。
同理：表里没有「系统主题/壁纸」就不要映射到 screen，没有「唤醒词/音区」就不要映射到
voice_assistant，没有「安全带提醒/碰撞预警等级」就不要映射到任何 ADAS 对象。

只输出 JSON 数组，每项 {{"i":序号,"object":"...","operate":"...","mode":"...","attr":""}}，
不确定就填 NONE。不要解释。"""


def edge_owned_misses() -> list[dict]:
    rows = [json.loads(l) for l in _CORPUS.open(encoding="utf-8")]
    sub = [(i, r) for i, r in enumerate(rows) if r.get("edge_expected") is not False]
    miss = [{"idx": i, "text": r["text"], "domain": r.get("domain") or "",
             "object": r.get("object") or "(空)"}
            for i, r in sub if classify_structured(r["text"]) is None]
    return sub, miss


def stratified(miss: list[dict], n: int, seed: int = 7) -> list[dict]:
    """按 corpus object 比例分层抽样。**不用头切片**——miss 在文件里是按对象成块排布的，
    头切片会只抽到导航/辅助驾驶两族，把天花板测成那两族的天花板。"""
    rnd = random.Random(seed)
    by = collections.defaultdict(list)
    for m in miss:
        by[m["object"]].append(m)
    out = []
    for obj, rows in sorted(by.items(), key=lambda x: -len(x[1])):
        k = max(1, round(n * len(rows) / len(miss)))
        out.extend(rnd.sample(rows, min(k, len(rows))))
    rnd.shuffle(out)
    return out[:n] if len(out) > n else out


def verify(row: dict, objs: dict) -> tuple[bool, str]:
    """结构校验 LLM 的提议。返回 (可执行, 拒绝原因)。"""
    o = str(row.get("object") or "").strip()
    if not o or o.upper() == "NONE":
        return False, "llm_none"
    spec = objs.get(o)
    if spec is None:
        return False, "unknown_object"
    op = str(row.get("operate") or "").strip()
    if op and op not in (spec.get("operates") or []):
        return False, "operate_not_allowed"
    if not op:
        return False, "no_operate"
    mode = str(row.get("mode") or "").strip()
    if mode and mode not in [str(m) for m in (spec.get("modes") or [])]:
        return False, "mode_not_in_table"
    attr = str(row.get("attr") or "").strip()
    if attr and attr not in [str(a) for a in (spec.get("attrs") or [])]:
        return False, "attr_not_in_table"
    return True, ""


def _make_llm(spec: str):
    import grpc

    from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
    addr = os.getenv("LLM_GATEWAY_ADDR", "localhost:50052")
    stub = llm_pb2_grpc.LLMGatewayStub(grpc.insecure_channel(addr))

    def _call(batch: list[dict], provider: str, model: str) -> str:
        lines = "\n".join(f"{i}. {it['text']}" for i, it in enumerate(batch, 1))
        req = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role="system", content=_SYSTEM.format(spec=spec)),
                      llm_pb2.Message(role="user", content=lines)],
            model=model or "@fast", temperature=0.0, max_tokens=1500)
        req.meta["thinking"] = "off"
        req.meta["caller_service"] = "eval-edge-ceiling"
        if provider:
            req.meta["llm_provider"] = provider
        return stub.Complete(req, timeout=90).content

    return _call


def _parse(raw: str, batch: list[dict]) -> list[dict]:
    s = raw.strip()
    a, b = s.find("["), s.rfind("]")
    if a < 0 or b <= a:
        return []
    try:
        arr = json.loads(s[a:b + 1])
    except Exception:
        return []
    out = []
    for it in arr if isinstance(arr, list) else []:
        if not isinstance(it, dict):
            continue
        try:
            i = int(it.get("i"))
        except (TypeError, ValueError):
            continue
        if not 1 <= i <= len(batch):
            continue
        src = batch[i - 1]
        out.append({"idx": src["idx"], "text": src["text"], "object": src["object"],
                    "domain": src["domain"], "llm_object": str(it.get("object") or ""),
                    "llm_operate": str(it.get("operate") or ""),
                    "llm_mode": str(it.get("mode") or ""), "llm_attr": str(it.get("attr") or "")})
    return out


def _load_done() -> dict:
    if not _SAMPLES.is_file():
        return {}
    out = {}
    for line in _SAMPLES.open(encoding="utf-8"):
        try:
            r = json.loads(line)
            out[int(r["idx"])] = r
        except Exception:
            continue
    return out


async def run_llm(sample: list[dict], spec: str, provider: str, model: str,
                  batch_size: int, concurrency: int) -> None:
    done = _load_done()
    todo = [s for s in sample if s["idx"] not in done]
    if not todo:
        print(f"LLM 标注：{len(sample)} 条已全部完成")
        return
    print(f"LLM 标注：待跑 {len(todo)} 条（已完成 {len(sample) - len(todo)}）")
    call = _make_llm(spec)
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    _SAMPLES.parent.mkdir(parents=True, exist_ok=True)

    async def one(batch, bi):
        async with sem:
            try:
                rows = _parse(await asyncio.to_thread(call, batch, provider, model), batch)
            except Exception as e:
                print(f"  [{bi}/{len(batches)}] 批失败：{type(e).__name__}: {str(e)[:110]}")
                return
            if not rows:
                print(f"  [{bi}/{len(batches)}] 解析失败（重跑本命令自动补）")
                return
            async with lock:
                with _SAMPLES.open("a", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"  [{bi}/{len(batches)}] +{len(rows)}")

    await asyncio.gather(*(one(b, i + 1) for i, b in enumerate(batches)))


def report(sub, miss, sample, objs, meta: dict) -> int:
    done = _load_done()
    labeled = [done[s["idx"]] for s in sample if s["idx"] in done]
    if not labeled:
        print("✗ 没有任何已标注样本——先跑一次（不加 --report-only）")
        return 1
    ok, reasons, online_only = 0, collections.Counter(), 0
    by_obj = collections.defaultdict(lambda: [0, 0])
    for r in labeled:
        good, why = verify({"object": r["llm_object"], "operate": r["llm_operate"],
                            "mode": r["llm_mode"], "attr": r["llm_attr"]}, objs)
        r["executable"] = good
        r["reject"] = why
        by_obj[r["object"]][1] += 1
        if good:
            ok += 1
            by_obj[r["object"]][0] += 1
            # **覆盖率 ≠ 本地执行率**：online_only 的对象（weather/page/app/news…）识别成功
            # 也照样上云——它们进「覆盖率」这个指标（口径就是 classify_structured 不返回
            # None），但永远不会在端侧执行。两个数必须分开报，否则「覆盖率涨了」会被误读
            # 成「更多请求不上云了」。
            if str((objs.get(r["llm_object"]) or {}).get("online")) != "offline_ok":
                online_only += 1
        else:
            reasons[why] += 1
    frac = ok / len(labeled)
    local_frac = (ok - online_only) / len(labeled)
    cov = 1 - len(miss) / len(sub)
    ceiling = cov + (len(miss) / len(sub)) * frac
    out = {
        "meta": meta | {"generated": datetime.now(timezone.utc).isoformat(),
                        "commit": git_short_sha()},
        "denominator": {"edge_owned_rows": len(sub), "miss": len(miss),
                        "coverage": round(cov, 4)},
        "sample": {"n_planned": len(sample), "n_labeled": len(labeled),
                   "executable": ok, "executable_frac": round(frac, 4),
                   "online_only_among_executable": online_only,
                   "locally_executable_frac": round(local_frac, 4)},
        "ceiling": round(ceiling, 4),
        "ceiling_local_only": round(cov + (len(miss) / len(sub)) * local_frac, 4),
        "reject_reasons": dict(reasons),
        "by_object": {k: {"executable": v[0], "n": v[1]} for k, v in
                      sorted(by_obj.items(), key=lambda x: -x[1][1])},
    }
    _REPORT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# 端侧覆盖率天花板（M5 P3 可行性判据）", "",
          f"> 生成 {out['meta']['generated']}　commit {out['meta']['commit']}　"
          f"provider {meta.get('provider') or '(未 pin)'}:{meta.get('model') or ''}", "",
          f"- 端侧应接子集：**{len(sub)}** 行（`edge_expected != false`）",
          f"- 规则 miss：**{len(miss)}** 行 → 现覆盖率 **{cov:.1%}**",
          f"- 抽样 {len(labeled)} 条 miss（按 corpus object 分层比例），经 commands.yaml 结构核验"
          f"**可执行 {ok} 条 = {frac:.1%}**",
          "",
          f"## 天花板 = {cov:.1%} + {len(miss)/len(sub):.1%} × {frac:.1%} = **{ceiling:.1%}**", "",
          "这是**上界**，三条必须一起读：",
          "1. 真分类器还会有分类错误与槽位抽取失败，实际必低于它；",
          f"2. **覆盖率 ≠ 本地执行率**：可执行的 {ok} 条里有 **{online_only}** 条落在 "
          f"`online_only` 对象（weather/page/app/news…）——识别成功也照样上云。"
          f"只算真能在端侧落地的，天花板是 **{cov + (len(miss)/len(sub))*local_frac:.1%}**；",
          "3. 结构核验只挡得住「发明不存在的 object/mode」，**挡不住语义错配**"
          "（把「速度控制模式」映射到 driving_mode 的 sport 这类「最接近的东西」）。"
          "prompt 已用三条真实反例明确要求「同一个东西而非最接近的东西」，但仍需人工抽查——"
          "抽查结论记在本文件下方或 RFC 里，不要只看这个数。",
          "", "## 不可执行的原因分布", "",
          "| 原因 | 条数 |", "|---|---|"]
    _WHY = {"llm_none": "表里没有能执行它的对象（模型自己判 NONE）",
            "unknown_object": "模型给了不存在的 object（幻觉，已被结构核验挡下）",
            "operate_not_allowed": "对象存在但不支持该操作",
            "mode_not_in_table": "对象存在但该模式不在表里",
            "attr_not_in_table": "对象存在但该属性不在表里",
            "no_operate": "模型没给出 operate"}
    for k, v in reasons.most_common():
        md.append(f"| {_WHY.get(k, k)} | {v} |")
    md += ["", "## 分对象（抽样内）", "", "| corpus object | 可执行/抽样 |", "|---|---|"]
    for k, v in sorted(by_obj.items(), key=lambda x: -x[1][1]):
        md.append(f"| {k} | {v[0]}/{v[1]} |")
    _REPORT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n=== 端侧应接子集 {len(sub)} 行，miss {len(miss)}，现覆盖 {cov:.1%} ===")
    print(f"抽样 {len(labeled)} 条 → 结构核验可执行 {ok}（{frac:.1%}），"
          f"其中 online_only {online_only} 条（识别得了但仍上云）")
    print(f"**天花板 {ceiling:.1%}**（识别口径，DoD 目标 85%）／"
          f"**{cov + (len(miss)/len(sub))*local_frac:.1%}**（只算能在端侧真落地的）")
    print(f"不可执行原因：{dict(reasons)}")
    print(f"报告 → {_REPORT_MD}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=150)
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--provider", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    objs = load_val_objects()
    if not objs:
        print("✗ 读不到 commands.yaml 的 objects")
        return 1
    sub, miss = edge_owned_misses()
    sample = stratified(miss, args.sample)
    print(f"VAL 可执行 object {len(objs)} 个；端侧应接 {len(sub)} 行 / miss {len(miss)} / "
          f"抽样 {len(sample)}")
    os.environ.setdefault("LLM_GATEWAY_ADDR", "localhost:50052")
    port, host = os.getenv("AUDIO_HTTP_PORT", "50059"), os.getenv(
        "LLM_GATEWAY_HTTP_HOST", "localhost")
    lock = ProviderLock(f"http://{host}:{port}", args.provider, args.model)
    if not args.report_only:
        lock.pin()
        asyncio.run(run_llm(sample, render_spec(objs), args.provider, args.model,
                            args.batch_size, args.concurrency))
        lock.check("after")
    meta = {"provider": args.provider, "model": args.model,
            "provider_lock": lock.summary()}
    return report(sub, miss, sample, objs, meta)


if __name__ == "__main__":
    sys.exit(main())
