"""端到端验证 R3.2 服务间 mTLS。

前置：先 scripts/gen-certs.ps1|sh 生成 certs/，再以 GRPC_TLS=on 起全栈（shell env 注入，勿动 .env）：
    GRPC_TLS=on make up          # 或 GRPC_TLS=on docker compose -f compose.yaml up --build -d
用法：  python test/e2e_mtls.py
依赖：  pip install websockets（grpcio 已随项目安装）

验收（对齐审计 T3.2）：① 业务链路走 mTLS mesh 仍通（edge-gw→edge-orch→cloud-gw→planner→chitchat
全程 mTLS）；② TLS 强制：宿主 insecure gRPC 探针打 registry:50051 握手失败（server 强制 TLS+client cert）。
"""
import asyncio
import json
import sys

try:                                   # Windows 控制台默认 GBK，强制 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import grpc

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

WS = "ws://localhost:8090/ws"
REGISTRY = "localhost:50051"
TIMEOUT = 60


async def _cloud_roundtrip() -> dict:
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"text": "讲个笑话", "session_id": "mtls-1"}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
            if msg.get("type") in ("final", "error"):
                return msg


def _insecure_probe_rejected() -> bool:
    """insecure 客户端连 TLS registry：握手失败 → channel 永不 ready → 超时=被拒（预期）。"""
    ch = grpc.insecure_channel(REGISTRY)
    try:
        grpc.channel_ready_future(ch).result(timeout=6)
        return False   # 竟然 ready = TLS 未强制
    except grpc.FutureTimeoutError:
        return True
    finally:
        ch.close()


async def main() -> int:
    print("=== E2E 服务间 mTLS（R3.2）===\n")
    fails = 0

    # 1) 业务链路走 mTLS mesh 仍通（云端 chitchat 全链路 mTLS）
    try:
        r = await _cloud_roundtrip()
        ok = r.get("type") == "final" and bool(r.get("speech"))
        print(f"{'✓' if ok else '✗'} mTLS mesh 云端链路：{r.get('speech', '')[:36]}")
        fails += 0 if ok else 1
    except Exception as e:
        print(f"✗ mTLS 云端链路异常：{type(e).__name__}: {e}")
        fails += 1

    # 2) 强制：insecure 探针打 TLS registry 应被拒
    try:
        if _insecure_probe_rejected():
            print("✓ insecure 探针被 TLS registry 拒绝（强制 TLS + client cert）")
        else:
            print("✗ insecure 探针竟连上 registry —— TLS 未强制？")
            fails += 1
    except Exception as e:
        print(f"✗ insecure 探针异常：{type(e).__name__}: {e}")
        fails += 1

    print(f"\n=== e2e_mtls: {'ALL PASS' if fails == 0 else str(fails) + ' FAIL'} ===")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
