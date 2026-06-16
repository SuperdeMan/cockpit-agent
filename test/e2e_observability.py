"""专项端到端可观测验证：中枢分发 → agent/VAL/tool 执行 → 仪表盘状态变更。

前置：`make up` 起全栈 + 真实 LLM_API_KEY（复杂多意图需 LLM 规划）。
依赖：pip install websockets

对每条指令，经 collector 三维观测：
  ① 中枢分发链路（obs.span：route.* / cloud.planning / step.agent|edge|tool / aggregate）
  ② 仪表盘车辆状态变更（/api/vehicle/state 前后 diff）
  ③ agent/确认执行状态（span status、need_confirm）

用法：python test/e2e_observability.py
"""
import asyncio
import json
import sys
import time
import uuid
import urllib.error
import urllib.request

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

# Windows 控制台默认 GBK，强制 UTF-8 以输出 •/→/✗ 等符号
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EDGE_WS = "ws://localhost:8090/ws"
COLLECTOR = "http://localhost:8092"


def _get(path: str):
    with urllib.request.urlopen(COLLECTOR + path, timeout=5) as r:
        return json.loads(r.read().decode())


def _post_debug(key: str, value):
    data = json.dumps({"key": key, "value": value}).encode()
    req = urllib.request.Request(
        COLLECTOR + "/api/debug/vehicle", data=data,
        headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        return {"ok": False, "error": str(e)}


def _gen_trace() -> str:
    return uuid.uuid4().hex[:16]


async def send(text, session, trace_id, is_confirmation=False, quiet=12, total=150):
    """发指令；收集所有 final（本地+云端可能多个），quiet 秒无新事件即收尾。"""
    async with websockets.connect(EDGE_WS, max_size=None) as ws:
        await ws.send(json.dumps({
            "text": text, "session_id": session,
            "is_confirmation": is_confirmation, "meta": {"trace_id": trace_id}}))
        finals = []
        start = time.time()
        got = False
        while time.time() - start < total:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=quiet))
            except asyncio.TimeoutError:
                if got:
                    break
                continue
            if msg.get("type") in ("final", "error"):
                finals.append(msg)
                got = True
        return finals


def _fmt_changes(ch):
    if not ch:
        return ""
    return ",".join(f"{c['key']}:{c['old']}→{c['new']}" for c in ch)


async def run_case(case):
    print("\n" + "=" * 72)
    print(f"【{case['name']}】\n  指令: {case['text']}")
    for k, v in case.get("setup", {}).items():
        print(f"  前置 debug: set {k}={v} → {_post_debug(k, v)}")
    if case.get("setup"):
        time.sleep(1.5)

    before = _get("/api/vehicle/state")
    tid = _gen_trace()
    finals = await send(case["text"], "obs-" + case["name"], tid,
                        case.get("is_confirmation", False))
    time.sleep(2.5)  # 等 collector best-effort 落库
    after = _get("/api/vehicle/state")
    try:
        tr = _get(f"/api/traces/{tid}")
        spans = sorted(tr.get("spans", []) if isinstance(tr, dict) else [],
                       key=lambda s: s.get("ts", 0))
    except Exception:
        spans = []

    speeches = [f.get("speech") or f.get("message", "") for f in finals]
    need = any(f.get("need_confirm") for f in finals)
    print(f"  回复({len(finals)} final): " +
          " || ".join(s[:64] for s in speeches if s))
    if need:
        print("  ⏸ need_confirm=True（危险动作二次确认）")

    print(f"  ── 中枢分发链路（{len(spans)} span，按时序）──")
    for s in spans:
        a = s.get("attrs", {}) or {}
        line = f"    • {s.get('node')} [{s.get('status')}]"
        if s.get("duration_ms"):
            line += f" {s.get('duration_ms')}ms"
        if a.get("intent"):
            line += f"  intent={a['intent']}"
        if a.get("complexity"):
            line += f"  complexity={a['complexity']}"
        ch = _fmt_changes(a.get("changes"))
        if ch:
            line += f"  diff={ch}"
        print(line)

    diff = {k: (before.get(k), after.get(k))
            for k in after if before.get(k) != after.get(k)}
    print(f"  ── 仪表盘车辆状态变更（{len(diff)} 项）──")
    print("    " + ("; ".join(f"{k}: {v[0]}→{v[1]}" for k, v in diff.items())
                    if diff else "（无）"))
    return {"spans": spans, "diff": diff, "finals": finals, "need": need}


CASES = [
    {"name": "T0纯车控", "text": "打开空调26度"},
    {"name": "T0多车控并行", "text": "空调调到22度，音量调到30"},
    {"name": "安全门控", "setup": {"speed_kmh": 130}, "text": "打开车窗"},
    {"name": "云端单Agent导航", "setup": {"speed_kmh": 0}, "text": "导航去北京南站"},
    {"name": "危险动作确认", "text": "打开后备箱"},
    {"name": "混合意图", "text": "打开主驾座椅加热，然后导航去首都机场"},
    {"name": "复杂多意图", "text": (
        "空调帮我开到23度，车窗开条缝，然后播一首周杰伦的歌，天窗开一半，"
        "座椅加热和座椅通风安排上，导航去离公司最近的粤菜馆，途中帮我找个咖啡店"
        "我要买杯咖啡，氛围灯也调成橙色，对了音量帮我调大点，好了出发吧")},
]


async def main():
    print("=" * 72)
    print("专项可观测验证：中枢分发 → agent/VAL/tool 执行 → 仪表盘状态变更")
    try:
        print(f"collector healthz: {_get('/healthz')}")
    except Exception as e:
        print(f"collector 不可达，请先 make up：{e}")
        return

    results = []
    for c in CASES:
        try:
            results.append((c["name"], await run_case(c)))
        except Exception as e:
            print(f"  ✗ 用例异常: {e}")

    print("\n" + "=" * 72 + "\n汇总（分发节点 | 车辆状态变更数）")
    for name, r in results:
        nodes = " → ".join(s["node"] for s in r["spans"]) or "(无 span)"
        flag = " ⏸需确认" if r["need"] else ""
        print(f"  • {name}: {len(r['diff'])}项变更{flag}\n      {nodes}")


if __name__ == "__main__":
    asyncio.run(main())
