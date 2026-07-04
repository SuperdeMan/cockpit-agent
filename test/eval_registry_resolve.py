"""T4.1/P1 Registry Resolve 路由评测：golden query → expected top-1 agent。

模式照抄 T3.4（eval_fast_intent/eval_route_hints）：直调既有函数产出准确率报告
（JSON+Markdown），可与已入库基线逐例 diff（跌破即 ::warning::、不阻塞）。

两层：
  - **关键词层（离线、CI 可复现、写基线）**：把真实 agents/*/manifest.yaml 注册进内存
    `registry.store.Store`，跑 `resolve()`（纯字符命中打分，零 PG/embed 依赖）。评测
    `requires_embed: false` 的召回 + 反例（车控/媒体不被吸走、闲聊不命中支付）。
  - **语义层（`--semantic`，需活 registry :50051 + LLM_EMBED_API_KEY）**：直连运行中的
    registry gRPC `ResolveAgents` 跑**全量**（含 `requires_embed: true`），验证 P0 真向量
    路由。不写基线（CI 无活栈），仅本地全量验证用。

不改动 registry 任何业务逻辑；不替代 registry/tests/ 原单测。

用法：
  python test/eval_registry_resolve.py                  # 离线关键词层，和基线比对（不阻塞）
  python test/eval_registry_resolve.py --write-baseline # 生成/覆盖基线（人工审阅后提交）
  python test/eval_registry_resolve.py --dump           # 打印每条 query 的关键词 top-3（校准用）
  python test/eval_registry_resolve.py --semantic       # 直连活 registry 跑全量语义层
  python test/eval_registry_resolve.py --strict         # 有回归时 exit 1（v1 未在 CI 启用）
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import yaml

try:                                   # Windows 控制台默认 GBK，强制 UTF-8（同 e2e_ws.py 惯例）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_common import (  # noqa: E402
    build_report, diff_against_baseline, load_baseline, print_ci_annotations,
    render_markdown, write_report, CaseResult,
)

sys.path.insert(0, str(_ROOT))
_gen_py = _ROOT / "gen" / "python"          # cockpit.* proto 生成代码（同根 conftest.py 惯例）
if _gen_py.is_dir():
    sys.path.insert(0, str(_gen_py))
from registry.store import Store  # noqa: E402
from agents._sdk.manifest import load_manifest  # noqa: E402

_CASES_PATH = _ROOT / "test" / "eval_corpus" / "registry_resolve_cases.yaml"
_DEFAULT_BASELINE = _ROOT / "docs" / "reviews" / "eval" / "baseline_registry_resolve.json"


def _load_cases() -> list[dict]:
    with open(_CASES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _build_store() -> Store:
    """真实 agents/*/manifest.yaml 注册进内存 Store（不硬编码 agent 列表——新 Agent 声明
    manifest 后本评测自动纳入，同 eval_route_hints 精神）。"""
    store = Store()
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = load_manifest(path)
        store.register(m, f"{m.agent_id}:0")
    return store


def _keyword_top(store: Store, query: str, top_k: int = 3) -> list[tuple]:
    recs = store.resolve("", query, top_k, [])
    return [(r.manifest.agent_id, round(s, 3)) for r, s in recs]


def _case_result(c: dict, top_list: list[tuple]) -> CaseResult:
    top1 = top_list[0][0] if top_list else None
    if "forbid_top1" in c:
        bucket = "resolve_guardrail"                     # 反例：某 Agent 不得成为 top-1
        expected = f"!= {c['forbid_top1']}"
        passed = top1 != c["forbid_top1"]
    else:
        bucket = "resolve_recall"                        # 召回：期望 top-1
        expected = c["expect_top1"]
        passed = top1 == c["expect_top1"]
    return CaseResult(
        id=f"resolve::{c['text']}", bucket=bucket, text=c["text"],
        expected=expected, actual=top1, passed=passed,
        detail=str(top_list), tags=c.get("tags", []),
        source=f"{_CASES_PATH.relative_to(_ROOT).as_posix()}",
    )


# ── 语义层（--semantic，直连活 registry）─────────────────────────────────────

def _semantic_top(query: str, top_k: int = 3) -> list[tuple]:
    import grpc
    from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc
    ch = grpc.insecure_channel("localhost:50051")
    stub = registry_pb2_grpc.RegistryStub(ch)
    resp = stub.ResolveAgents(registry_pb2.ResolveRequest(intent="", query=query, top_k=top_k))
    return [(a.manifest.agent_id, round(a.score, 3)) for a in resp.agents]


def _run_semantic(raw_cases: list[dict]) -> int:
    """直连活 registry 跑全量（含 requires_embed）。仅打印，不写基线。"""
    ok = 0
    graded = 0
    for c in raw_cases:
        top_list = _semantic_top(c["text"])
        res = _case_result(c, top_list)
        graded += 1
        ok += res.passed
        print(f"  {'OK ' if res.passed else 'XX '} [{res.bucket}] {c['text']!r} "
              f"expect={res.expected} got={top_list}")
    print(f"\n语义层（活 registry）：{ok}/{graded} 通过")
    return 0 if ok == graded else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="T4.1/P1 Registry Resolve 路由评测")
    ap.add_argument("--baseline", default=str(_DEFAULT_BASELINE))
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--out-json")
    ap.add_argument("--out-md")
    ap.add_argument("--strict", action="store_true",
                     help="有回归时 exit 1；默认不启用，CI 走非阻塞观测")
    ap.add_argument("--dump", action="store_true",
                     help="打印每条 query 的关键词 top-3（校准 cases yaml 时用）")
    ap.add_argument("--semantic", action="store_true",
                     help="直连活 registry(:50051) 跑全量语义层（需 LLM_EMBED_API_KEY + make up）")
    args = ap.parse_args()

    raw_cases = _load_cases()

    if args.dump:
        store = _build_store()
        for c in raw_cases:
            print(f"text={c['text']!r}  expect={c.get('expect_top1') or ('!=' + c.get('forbid_top1', ''))}"
                  f"  requires_embed={bool(c.get('requires_embed'))}\n"
                  f"  keyword_top3={_keyword_top(store, c['text'])}\n")
        return 0

    if args.semantic:
        return _run_semantic(raw_cases)

    # 默认：离线关键词层，只评 requires_embed=false 的用例（语义用例需活栈，见 --semantic）。
    store = _build_store()
    kw_cases = [c for c in raw_cases if not c.get("requires_embed")]
    cases = [_case_result(c, _keyword_top(store, c["text"])) for c in kw_cases]
    n_embed = len(raw_cases) - len(kw_cases)
    sources = [
        {"path": f"{_CASES_PATH.relative_to(_ROOT).as_posix()} (keyword layer)", "count": len(kw_cases)},
        {"path": f"{_CASES_PATH.relative_to(_ROOT).as_posix()} (requires_embed, 离线跳过→见 --semantic)",
         "count": n_embed},
    ]
    report = build_report("registry_resolve", sources, cases)
    md = render_markdown(report)

    for c in cases:
        print(f"  {'PASS' if c.passed else 'FAIL'}  [{c.bucket}] {c.text!r}")
    overall = report["overall"]
    print(f"\n总计 {overall['passed']}/{overall['total']} passed"
          f"（另 {n_embed} 条 requires_embed 离线跳过）")

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
    print_ci_annotations("eval_registry_resolve", diff, baseline_path)
    return 1 if (diff.has_regressions and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
