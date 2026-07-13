"""R4.4 受话判定（拒识）评测：golden 句 → 期望 addressed 布尔。

模式照抄 eval_registry_resolve.py 的「离线 / --live 两态」，报告工具复用 eval_common
（与 fast_intent/route_hints/registry_resolve 三套同款：JSON+Markdown 基线，逐例 diff，
跌破即 ::warning:: 非阻塞）。**只评 Planner 的受话判定质量**，不测 engine 短路/HMI
（那些是 pytest 单测 test_engine_reject.py 的职责）。

两态：
  - 离线（无参，CI 可跑）：校验 rejection_cases.yaml 结构 + `_planner_system()` 含受话段。
    不调 LLM（受话判定质量是 LLM 行为，纯规则测不了），故离线只做「尺子自检」。
  - `--live`：把每条 text 拼进 `_planner_system()` + 真实 catalog → llm-gateway `Complete`
    → 解析顶层 `addressed` → 分桶（accept/reject）统计 + JSON 解析失败率 → 产
    `docs/reviews/eval/baseline_rejection.{json,md}`（含当时 active provider，从
    `/api/llm/providers` 读）。多 provider 通用：脚本不感知厂商，切 provider 重跑即分家基线。

用法：
  python test/eval_rejection.py                  # 离线尺子自检（CI）
  python test/eval_rejection.py --live           # 直连活栈打真 LLM，写基线
  python test/eval_rejection.py --live --strict  # 有回归时 exit 1（默认非阻塞观测）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
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

_CASES_PATH = _ROOT / "test" / "eval_corpus" / "rejection_cases.yaml"
_DEFAULT_BASELINE = _ROOT / "docs" / "reviews" / "eval" / "baseline_rejection.json"
_CLARIFY_CASES_PATH = _ROOT / "test" / "eval_corpus" / "clarify_cases.yaml"
_CLARIFY_BASELINE = _ROOT / "docs" / "reviews" / "eval" / "baseline_clarify.json"


def _load_cases(path: Path = _CASES_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _planner_system_text() -> str:
    """取 Planner 受话段拼好的 system prompt（T1 后 _planner_system() 存在；早于 T1 兼容旧常量）。"""
    from orchestrator.cloud import planning
    fn = getattr(planning, "_planner_system", None)
    if callable(fn):
        return fn()
    return getattr(planning, "_PLANNER_SYSTEM", "")


def _parse_addressed(raw: str) -> tuple[bool, bool]:
    """(addressed, parse_ok)。addressed 仅显式 false 生效，其余（缺省/垃圾/解析失败）=True（fail-open）。"""
    from orchestrator.cloud.planning import PlanBuilder
    try:
        data = json.loads(PlanBuilder._extract_json(raw or ""))
    except (json.JSONDecodeError, ValueError):
        return True, False
    if not isinstance(data, dict):
        return True, False
    return (data.get("addressed") is not False), True


# ── 离线：尺子自检 ────────────────────────────────────────────────────────────

def _run_offline(raw_cases: list[dict]) -> int:
    errs: list[str] = []
    n_accept = n_reject = 0
    for i, c in enumerate(raw_cases):
        if not isinstance(c, dict) or not c.get("text"):
            errs.append(f"#{i} 缺 text")
            continue
        exp = c.get("expect")
        if exp not in ("accept", "reject"):
            errs.append(f"{c.get('text')!r} expect 非法：{exp!r}")
        n_accept += exp == "accept"
        n_reject += exp == "reject"
    if n_accept < 25:
        errs.append(f"正例不足 25（当前 {n_accept}）")
    if n_reject < 15:
        errs.append(f"负例不足 15（当前 {n_reject}）")

    # 受话段自检：T1 落地后 prompt 必含「受话判定」与「addressed」。早于 T1 只提示不失败。
    sys_text = _planner_system_text()
    if "addressed" in sys_text and "受话" in sys_text:
        print("  [OK] _planner_system() 含受话判定段")
    else:
        print("  [提示] _planner_system() 暂未含受话段（T1 前正常；T1 后此项须转 OK）")

    print(f"\n离线尺子自检：正例 {n_accept} / 负例 {n_reject} / 共 {len(raw_cases)}")
    if errs:
        print("  校验失败：")
        for e in errs:
            print(f"    - {e}")
        return 1
    print("  语料结构 OK")
    return 0


# ── --live：直连活栈打真 LLM ──────────────────────────────────────────────────

def _build_catalog() -> str:
    """真实 agents/*/manifest.yaml → render_catalog 文本（不硬编码 agent 列表，同 eval_route_hints
    精神：新 Agent 声明 manifest 后自动纳入）。"""
    from types import SimpleNamespace
    from orchestrator.cloud.context import WorkingSet
    from agents._sdk.manifest import load_manifest
    agents = []
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = load_manifest(path)
        agents.append(SimpleNamespace(manifest=m, endpoint=""))
    return WorkingSet.render_catalog(agents)


def _active_provider() -> str:
    """best-effort 读 llm-gateway active provider（HTTP :AUDIO_HTTP_PORT/api/llm/providers）。"""
    import urllib.request
    port = os.getenv("AUDIO_HTTP_PORT", "50059")
    host = os.getenv("LLM_GATEWAY_HTTP_HOST", "localhost")
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/llm/providers", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        act = data.get("active", {})
        return f"{act.get('provider', '?')}:{act.get('model', '?')}"
    except Exception:
        return os.getenv("LLM_PROVIDER", "unknown")


def _llm_complete(messages: list[dict]) -> str:
    """直连 llm-gateway gRPC Complete（不复用 Clients 以免拖入全套连接管理；同 eval_registry
    --semantic 直连惯例）。Planner 恒不开思考。"""
    import grpc
    from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
    addr = os.getenv("LLM_GATEWAY_ADDR", "localhost:50052")
    ch = grpc.insecure_channel(addr)
    stub = llm_pb2_grpc.LLMGatewayStub(ch)
    req = llm_pb2.CompleteRequest(
        messages=[llm_pb2.Message(role=m["role"], content=m["content"]) for m in messages],
        temperature=0.3, max_tokens=800)
    # 观测归属（caller_service 仅观测、不扰动限流桶键 "caller"，惯例同 planner/SDK）
    req.meta["caller_service"] = "eval-rejection"
    resp = stub.Complete(req, timeout=30)
    return resp.content


def _run_live(raw_cases: list[dict], args) -> int:
    provider = _active_provider()
    if provider.startswith("mock"):
        print(f"::warning::active provider={provider}——mock 不判 addressed，--live 结果无意义"
              "（fail-open 恒 accept）。切真 provider 后重跑。")
    catalog = _build_catalog()
    sys_text = _planner_system_text()

    cases: list[CaseResult] = []
    parse_fail = 0
    for c in raw_cases:
        text = c["text"]
        user_msg = f"可用能力:\n{catalog}\n\n用户说: {text}"
        try:
            raw = _llm_complete([
                {"role": "system", "content": sys_text},
                {"role": "user", "content": user_msg},
            ])
        except Exception as e:
            print(f"  XX LLM 调用失败 {text!r}: {e}")
            raw = ""
        addressed, ok = _parse_addressed(raw)
        if not ok:
            parse_fail += 1
        if c["expect"] == "accept":
            bucket, expected = "accept_recall", "addressed=true"
            passed = addressed
        else:
            bucket, expected = "reject_guardrail", "addressed=false"
            passed = not addressed
        cases.append(CaseResult(
            id=f"reject::{text}", bucket=bucket, text=text,
            expected=expected, actual=f"addressed={addressed}", passed=passed,
            detail="" if ok else "json_parse_fail", tags=[c.get("tag", "")],
            source=f"{_CASES_PATH.relative_to(_ROOT).as_posix()}"))
        print(f"  {'PASS' if passed else 'FAIL'} [{bucket}] {text!r} → addressed={addressed}"
              f"{'' if ok else ' (解析失败)'}")

    sources = [{"path": f"{_CASES_PATH.relative_to(_ROOT).as_posix()}", "count": len(raw_cases)}]
    report = build_report("rejection", sources, cases)
    report["meta"]["provider"] = provider
    report["meta"]["json_parse_failures"] = parse_fail
    report["meta"]["json_parse_fail_rate"] = round(parse_fail / len(cases), 4) if cases else 0.0
    md = render_markdown(report)
    md += (f"\n> active provider：`{provider}`　JSON 解析失败：{parse_fail}/{len(cases)}"
           f"（{report['meta']['json_parse_fail_rate'] * 100:.1f}%）\n")

    acc = report["buckets"].get("accept_recall", {})
    rej = report["buckets"].get("reject_guardrail", {})
    print(f"\nprovider={provider}")
    print(f"正例误拒率 = {(1 - acc.get('pass_rate', 1)) * 100:.1f}%"
          f"（{acc.get('total', 0) - acc.get('passed', 0)}/{acc.get('total', 0)} 被误拒）")
    print(f"负例拦截率 = {rej.get('pass_rate', 0) * 100:.1f}%"
          f"（{rej.get('passed', 0)}/{rej.get('total', 0)} 正确拒识）")
    print(f"JSON 解析失败率 = {report['meta']['json_parse_fail_rate'] * 100:.1f}%")

    baseline_path = Path(args.baseline)
    if args.write_baseline or not baseline_path.exists():
        write_report(report, md, baseline_path, baseline_path.with_suffix(".md"))
        print(f"\n基线已写入 {baseline_path}")
        return 0

    if args.out_json and args.out_md:
        write_report(report, md, Path(args.out_json), Path(args.out_md))
    baseline = load_baseline(baseline_path)
    diff = diff_against_baseline(report, baseline)
    print_ci_annotations("eval_rejection", diff, baseline_path)
    return 1 if (diff.has_regressions and args.strict) else 0


# ── --clarify：路由歧义澄清评测 ───────────────────────────────────────────────

def _clarify_shown(raw: str) -> bool:
    """LLM 输出是否会让 engine 弹出 intent_choice 卡 = 有合法 clarify 且 steps 空（母卡 D6-2：
    steps 非空则 clarify 让位）。"""
    from orchestrator.cloud.planning import PlanBuilder
    try:
        data = json.loads(PlanBuilder._extract_json(raw or ""))
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    clarify = PlanBuilder._parse_clarify(data.get("clarify"))
    return clarify is not None and not data.get("steps")


def _run_clarify_offline(cases: list[dict]) -> int:
    errs = []
    n_clarify = n_direct = 0
    for c in cases:
        exp = c.get("expect")
        if exp not in ("clarify", "direct"):
            errs.append(f"{c.get('text')!r} expect 非法：{exp!r}")
        n_clarify += exp == "clarify"
        n_direct += exp == "direct"
    if n_direct < 15:
        errs.append(f"direct 硬门槛集不足 15（当前 {n_direct}）")
    os.environ["CLARIFY_ENABLED"] = "on"
    if "路由歧义澄清" in _planner_system_text():
        print("  [OK] CLARIFY_ENABLED=on 下 _planner_system() 含澄清段")
    else:
        errs.append("CLARIFY_ENABLED=on 下 _planner_system() 未含澄清段")
    print(f"\n离线尺子自检（clarify）：应澄清 {n_clarify} / 不得澄清 {n_direct}")
    if errs:
        for e in errs:
            print(f"    - {e}")
        return 1
    print("  语料结构 OK")
    return 0


def _run_clarify_live(cases: list[dict], args) -> int:
    os.environ["CLARIFY_ENABLED"] = "on"      # 拼入澄清段（消费端在 engine，这里只为 prompt）
    provider = _active_provider()
    if provider.startswith("mock"):
        print(f"::warning::active provider={provider}——mock 不判 clarify，结果无意义。")
    catalog = _build_catalog()
    sys_text = _planner_system_text()

    results: list[CaseResult] = []
    for c in cases:
        text = c["text"]
        user_msg = f"可用能力:\n{catalog}\n\n用户说: {text}"
        try:
            raw = _llm_complete([{"role": "system", "content": sys_text},
                                 {"role": "user", "content": user_msg}])
        except Exception as e:
            print(f"  XX LLM 调用失败 {text!r}: {e}")
            raw = ""
        shown = _clarify_shown(raw)
        if c["expect"] == "clarify":
            bucket, expected, passed = "clarify_recall", "出澄清卡", shown
        else:
            bucket, expected, passed = "clarify_guardrail", "不出澄清（直接执行）", not shown
        results.append(CaseResult(
            id=f"clarify::{text}", bucket=bucket, text=text, expected=expected,
            actual=f"clarify_shown={shown}", passed=passed, tags=[c.get("tag", "")],
            source=f"{_CLARIFY_CASES_PATH.relative_to(_ROOT).as_posix()}"))
        print(f"  {'PASS' if passed else 'FAIL'} [{bucket}] {text!r} → clarify_shown={shown}")

    sources = [{"path": f"{_CLARIFY_CASES_PATH.relative_to(_ROOT).as_posix()}", "count": len(cases)}]
    report = build_report("clarify", sources, results)
    report["meta"]["provider"] = provider
    md = render_markdown(report)
    md += f"\n> active provider：`{provider}`\n"

    rec = report["buckets"].get("clarify_recall", {})
    guard = report["buckets"].get("clarify_guardrail", {})
    print(f"\nprovider={provider}")
    print(f"应澄清命中 = {rec.get('passed', 0)}/{rec.get('total', 0)}")
    print(f"不得澄清（direct 硬门槛）= {guard.get('passed', 0)}/{guard.get('total', 0)}"
          f"（{guard.get('total', 0) - guard.get('passed', 0)} 条误澄清）")

    baseline_path = Path(args.baseline) if args.baseline != str(_DEFAULT_BASELINE) else _CLARIFY_BASELINE
    if args.write_baseline or not baseline_path.exists():
        write_report(report, md, baseline_path, baseline_path.with_suffix(".md"))
        print(f"\n基线已写入 {baseline_path}")
        return 0
    baseline = load_baseline(baseline_path)
    diff = diff_against_baseline(report, baseline)
    print_ci_annotations("eval_clarify", diff, baseline_path)
    return 1 if (diff.has_regressions and args.strict) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="R4.4 受话判定（拒识）/ 路由澄清评测")
    ap.add_argument("--live", action="store_true",
                    help="直连活栈 llm-gateway 打真 LLM（需真 provider + make up）")
    ap.add_argument("--clarify", action="store_true",
                    help="评路由歧义澄清（clarify_cases.yaml）而非拒识")
    ap.add_argument("--baseline", default=str(_DEFAULT_BASELINE))
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--out-json")
    ap.add_argument("--out-md")
    ap.add_argument("--strict", action="store_true",
                    help="有回归时 exit 1；默认非阻塞观测")
    args = ap.parse_args()

    if args.clarify:
        cases = _load_cases(_CLARIFY_CASES_PATH)
        return _run_clarify_live(cases, args) if args.live else _run_clarify_offline(cases)

    raw_cases = _load_cases()
    if args.live:
        return _run_live(raw_cases, args)
    return _run_offline(raw_cases)


if __name__ == "__main__":
    sys.exit(main())
