"""真栈闭环：受控 MCP 桥（M3 P2）。

五步：
1. **准入**：注册中心里 mcp-bridge 只有 servers.yaml 准入的能力（server 多提供的
   `order.cancel` 不在清单 → 不该出现）。
2. **只读工具**：「咖啡有什么」→ 经 MCP 拿到菜单，卡片带演示商户标注 + `_prov` 非真章。
3. **写操作确认链**：「点一杯大杯拿铁」→ **先确认**再下单（未确认时一次商户调用都没有）。
4. **幂等**：同一单再说一遍 → 商户复用原订单号，不双扣。
5. **补偿**：桥自身不暴露取消工具（清单未准入），但商户侧补偿路径存在——
   由 `agents/mcp_bridge/tests/test_bridge.py` 覆盖（真栈这里只验准入边界）。

前置：make up 全栈（含 mcp-bridge）。
用法：python test/e2e_mcp.py
"""
import asyncio
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "gen", "python"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

WS = "ws://localhost:8090/ws"
REGISTRY_ADDR = "localhost:50051"
SESSION = f"e2e-mcp-{int(time.time())}"
_results: list[bool] = []


def record(name, ok, detail=""):
    _results.append(bool(ok))
    print(f"{'✅' if ok else '❌'} {name}  {detail}")


async def ask(text: str, session: str = SESSION) -> dict:
    """一问一答，返回 final 帧（含 speech / cards / status）。"""
    async with websockets.connect(WS, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "user_text", "text": text,
                                  "session_id": session}))
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=90)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("type") == "final":
                return msg
    return {}


def reset_bridge() -> None:
    """重启 mcp-bridge = 演示商户净初态。

    演示 server 的订单表是**进程内**的（它是真实商户的替身，不该自建持久化）——
    不重置的话，第二遍跑「确认后真的下单了」拿到的是上一遍的幂等复用响应，
    那条断言就名不副实了。真实商户不需要这一步：幂等是按请求指纹算的，
    换一天/换一单自然就是新单。
    """
    subprocess.run(["docker", "compose", "restart", "mcp-bridge"],
                   capture_output=True, text=True, timeout=180)
    time.sleep(18)          # 覆盖 MCP 握手 + registry 重注册


def bridge_capabilities() -> list | None:
    """直接问注册中心：mcp-bridge 注册了哪些 intent（None=没注册上）。

    走 gRPC 而不是 collector 的 /api/agents——后者是健康快照，不带 capability 明细，
    而本用例要验的正是**准入边界**（清单外的工具不该出现在能力表里）。
    """
    try:
        import grpc
        from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc
    except ImportError:
        print("   ⚠️ 缺 gen/python（先 make proto），跳过注册中心断言")
        return None
    with grpc.insecure_channel(REGISTRY_ADDR) as ch:
        stub = registry_pb2_grpc.RegistryStub(ch)
        resp = stub.ListAgents(registry_pb2.ListRequest(), timeout=10)
    for a in resp.agents:                       # ResolvedAgent{manifest, endpoint, score}
        if a.manifest.agent_id == "mcp-bridge":
            return [c.intent for c in a.manifest.capabilities]
    return None


def cards_of(msg: dict) -> list:
    # WS final 帧的卡片键是 `ui_card`（不是 card/cards）——照 HMI App.tsx 的契约
    cards = [msg["ui_card"]] if msg.get("ui_card") else []
    out = []
    for c in cards:
        if isinstance(c, dict) and c.get("type") == "card_group":
            out.extend(c.get("items") or [])
        elif isinstance(c, dict):
            out.append(c)
    return out


async def main():
    reset_bridge()          # 净初态（见 reset_bridge 注释）
    print("── 1. 准入边界：注册中心只看得到清单里的能力 ──")
    caps = bridge_capabilities()
    record("mcp-bridge 已注册", caps is not None, "" if caps else "注册中心里没有")
    caps = caps or []
    record("准入的两个工具都在", {"shop.menu", "shop.order"} <= set(caps), str(caps))
    record("清单外的 order.cancel 未被注册（动态放行是不做项）",
           not any("cancel" in c for c in caps), str(caps))

    print("\n── 2. 只读工具经 MCP 取真数据 ──")
    res = await ask("看看咖啡菜单有什么")
    sp = res.get("speech", "")
    ok_read = "拿铁" in sp or "美式" in sp or "摩卡" in sp
    record("菜单经 MCP server 返回", ok_read, sp[:70])
    record("演示商户在话术里说明白", "演示商户" in sp, sp[:40])
    cards = cards_of(res)
    demo_card = next((c for c in cards if c.get("demo")), None)
    record("卡片带演示商户角标", bool(demo_card),
           str([c.get("type") for c in cards]))
    if demo_card:
        record("_prov 不盖真章（mode=mock）",
               (demo_card.get("_prov") or {}).get("mode") == "mock",
               str(demo_card.get("_prov")))
    else:
        record("_prov 不盖真章（mode=mock）", False, "无演示卡")

    print("\n── 3. 写操作：先确认 ──")
    sess = f"{SESSION}-order"
    res = await ask("点一杯大杯拿铁", sess)
    sp = res.get("speech", "")
    asked = "确认" in sp or res.get("status") == "need_confirm"
    record("下单先要二次确认（钱的事不能一句话就扣）", asked, sp[:70])

    print("\n── 4. 确认后下单 + 幂等 ──")
    res = await ask("确认", sess)
    sp1 = res.get("speech", "")
    ordered = ("订单" in sp1 or "下单" in sp1) and "已经下过了" not in sp1
    record("确认后真的下单了（新单，不是幂等复用）", ordered, sp1[:70])

    res = await ask("点一杯大杯拿铁", f"{SESSION}-order2")
    if "确认" in res.get("speech", ""):
        res = await ask("确认", f"{SESSION}-order2")
    sp2 = res.get("speech", "")
    record("同一单再说一遍不双扣（幂等键=请求指纹）",
           "已经下过了" in sp2 or "已存在" in sp2, sp2[:70])

    ok = sum(_results)
    print(f"\n===== e2e_mcp: {ok}/{len(_results)} =====")
    return 0 if ok == len(_results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
