"""端到端验证 R3.2 服务间 mTLS（由 manifest runner 配置 mTLS profile）。"""

import asyncio
import json
import sys

from support.e2e import CaseRecorder

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

REGISTRY = "localhost:50051"
TIMEOUT = 60


async def _cloud_roundtrip(recorder: CaseRecorder) -> dict:
    async with websockets.connect(recorder.ws_url()) as ws:
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
        recorder.confirm_identity_ack(ack)
        await ws.send(json.dumps({
            "text": "讲个笑话",
            "session_id": recorder.session_id(1),
        }))
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
    recorder = CaseRecorder()

    with recorder:
        try:
            result = await _cloud_roundtrip(recorder)
            ok = (
                result.get("type") == "final"
                and bool(result.get("speech"))
            )
            if ok:
                recorder.pass_case("mtls_mesh_cloud_roundtrip")
            else:
                recorder.fail_case(
                    "mtls_mesh_cloud_roundtrip",
                    "assertion_failed",
                    "mTLS cloud response was incomplete",
                )
            print(
                f"{'✓' if ok else '✗'} mTLS mesh 云端链路："
                f"{result.get('speech', '')[:36]}",
            )
        except Exception as exc:
            recorder.fail_case(
                "mtls_mesh_cloud_roundtrip",
                "unhandled_exception",
                f"mTLS cloud request raised {type(exc).__name__}",
            )
            print(f"✗ mTLS 云端链路异常：{type(exc).__name__}")

        try:
            rejected = _insecure_probe_rejected()
            if rejected:
                recorder.pass_case("insecure_registry_probe_rejected")
                print("✓ insecure 探针被 TLS registry 拒绝（强制 TLS + client cert）")
            else:
                recorder.fail_case(
                    "insecure_registry_probe_rejected",
                    "assertion_failed",
                    "insecure probe connected to the TLS registry",
                )
                print("✗ insecure 探针竟连上 registry —— TLS 未强制？")
        except Exception as exc:
            recorder.fail_case(
                "insecure_registry_probe_rejected",
                "unhandled_exception",
                f"insecure probe raised {type(exc).__name__}",
            )
            print(f"✗ insecure 探针异常：{type(exc).__name__}")

    failures = recorder.result.counts["failed"]
    print(
        f"\n=== e2e_mtls: "
        f"{'ALL PASS' if failures == 0 else str(failures) + ' FAIL'} ===",
    )
    return recorder.exit_code()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
