"""M1a 协议探针：各 provider tool-calling 真实行为矩阵（RFC 2026-07-24-m1a §7-1）。

文档钉不死的两个 ★ 由此钉死：① named tool_choice 真实支持度（被无视=返回文本）；
② 关思考（planner 恒关）下 tool_calls 稳定性。逐家经 llm-gateway gRPC Complete
（请求级 pin meta.llm_provider）发带 submit_plan 的规划请求——prompt/schema 直接
取生产实现（orchestrator.cloud.planning），探的就是生产形态。

只看协议形态（tool_calls 置位/arguments 合法/必填字段），不判路由内容质量
（那是 eval_mode_routing --live 的职责）。

前置：make up（llm-gateway + 目标厂商 key）。
用法：python test/e2e_planner_toolcall.py [--providers mimo,minimax] [--rounds 1]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 控制台 gbk 防崩

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT), str(_ROOT / "gen" / "python")):
    if p not in sys.path:
        sys.path.insert(0, p)

from orchestrator.cloud.planning import (  # noqa: E402
    _planner_system, _submit_plan_tools, _SUBMIT_PLAN_NAME, _date_line,
)

_CATALOG = (
    "可用能力:\n"
    "- hvac: hvac.set（空调控制：温度/风量/开关）\n"
    "- media: media.play（播放音乐/电台）\n"
    "- info: info.weather（天气查询）, info.search（联网搜索）\n"
    "- nearby: nearby.search（周边搜索餐厅/停车）, nearby.order（下单/订位）\n"
    "- chitchat: chitchat.talk（闲聊兜底）"
)

# 代表形态：单意图 / 多意图并行 / 依赖串行 / 受话 false
_PROBES = [
    ("单意图", "帮我把空调调到24度"),
    ("多意图", "打开空调顺便看看今天天气"),
    ("依赖串行", "找家川菜馆然后帮我订位"),
    ("受话false", "妈你到哪了"),
]


def _http_providers() -> dict | None:
    host = os.getenv("LLM_GATEWAY_HTTP_HOST", "localhost")
    port = os.getenv("AUDIO_HTTP_PORT", "50059")
    try:
        with urllib.request.urlopen(
                f"http://{host}:{port}/api/llm/providers", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"SKIP：llm-gateway HTTP 不可达（{e}）——需 make up 后再跑")
        return None


def _probe_one(stub, llm_pb2, provider: str, text: str) -> dict:
    """单次探针：返回 {tool, name_ok, args_ok, fields_ok, finish, err}。"""
    user_msg = f"{_CATALOG}\n\n{_date_line()}\n用户说: {text}"
    req = llm_pb2.CompleteRequest(
        messages=[
            llm_pb2.Message(role="system", content=_planner_system(toolcall=True)),
            llm_pb2.Message(role="user", content=user_msg),
        ],
        temperature=0.3, max_tokens=800)
    req.tools.update(_submit_plan_tools())
    req.meta["llm_provider"] = provider          # 请求级 pin（D2）：漂移 fail-closed
    req.meta["caller_service"] = "e2e-planner-toolcall"
    out = {"tool": False, "name_ok": False, "args_ok": False,
           "fields_ok": False, "finish": "", "err": "", "content_len": 0}
    try:
        resp = stub.Complete(req, timeout=60)
    except Exception as e:
        out["err"] = f"{type(e).__name__}: {str(e)[:160]}"
        return out
    out["finish"] = resp.finish_reason
    out["content_len"] = len(resp.content or "")
    if not resp.HasField("tool_calls"):
        return out
    from google.protobuf.json_format import MessageToDict
    data = MessageToDict(resp.tool_calls)
    calls = data.get("tool_calls") or []
    out["tool"] = bool(calls)
    args = next((c.get("arguments") for c in calls
                 if isinstance(c, dict) and c.get("name") == _SUBMIT_PLAN_NAME), None)
    out["name_ok"] = args is not None
    if isinstance(args, dict):
        out["args_ok"] = True
        steps = args.get("steps")
        out["fields_ok"] = (isinstance(args.get("addressed"), bool)
                            and isinstance(steps, list)
                            and all(isinstance(s, dict) and s.get("agent_id")
                                    and s.get("intent") for s in steps))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="",
                    help="逗号分隔（缺省=网关全部 available 厂商）")
    ap.add_argument("--rounds", type=int, default=1, help="每 provider×句 重复轮数")
    args = ap.parse_args()

    info = _http_providers()
    if info is None:
        return 0
    available = [p["id"] for p in info.get("providers", [])
                 if p.get("available") and p["id"] != "mock"]
    targets = ([p.strip() for p in args.providers.split(",") if p.strip()]
               if args.providers else available)
    bad = [t for t in targets if t not in available]
    if bad:
        print(f"⚠ 跳过未配置厂商：{bad}（available={available}）")
        targets = [t for t in targets if t in available]
    if not targets:
        print("SKIP：无可探厂商（全部未配置 key）")
        return 0
    print(f"探针目标：{targets}　active={info.get('active')}　rounds={args.rounds}\n")

    import grpc
    from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
    addr = os.getenv("LLM_GATEWAY_ADDR", "localhost:50052")
    stub = llm_pb2_grpc.LLMGatewayStub(grpc.insecure_channel(addr))

    rows = []
    all_ok = True
    for pid in targets:
        n = ok_tool = ok_args = ok_fields = 0
        finishes: dict[str, int] = {}
        errs: list[str] = []
        for _ in range(max(1, args.rounds)):
            for label, text in _PROBES:
                r = _probe_one(stub, llm_pb2, pid, text)
                n += 1
                ok_tool += r["tool"] and r["name_ok"]
                ok_args += r["args_ok"]
                ok_fields += r["fields_ok"]
                finishes[r["finish"] or "-"] = finishes.get(r["finish"] or "-", 0) + 1
                status = ("✓" if r["fields_ok"] else
                          "text" if not r["tool"] and not r["err"] else "✗")
                print(f"  [{pid}] {label}: {status} tool={r['tool']} "
                      f"finish={r['finish'] or '-'} "
                      + (f"err={r['err']}" if r["err"] else f"content_len={r['content_len']}"))
                if r["err"]:
                    errs.append(f"{label}: {r['err']}")
        rows.append((pid, n, ok_tool, ok_args, ok_fields, finishes, errs))
        if ok_fields < n:
            all_ok = False

    print("\n## Provider tool-calling 协议矩阵（named tool_choice=submit_plan 强制）\n")
    print("| provider | tool_calls 返回 | args 合法 | 必填字段齐 | finish_reason 分布 |")
    print("|---|---|---|---|---|")
    for pid, n, t, a, f, fin, errs in rows:
        print(f"| {pid} | {t}/{n} | {a}/{n} | {f}/{n} | "
              + ", ".join(f"{k}×{v}" for k, v in fin.items()) + " |")
    for pid, *_rest, errs in rows:
        for e in errs:
            print(f"  ! {pid} {e}")
    print("\n结论口径：tool_calls 返回 < 全数 = named tool_choice 被该家无视/协议失败"
          "（生产由轮内 salvage/JSON 回退承接，该家 fallback 率照此预估）。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
