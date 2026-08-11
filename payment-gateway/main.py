"""Payment Gateway 启动入口。契约 docs/conventions.md §9.17。"""
import asyncio
import os

# 结构化日志：stdout JSON 带 trace/session + obs.log 上报（badcase 按 trace 检索）
from observability import setup_structured_logging

setup_structured_logging(os.getenv("LOG_LEVEL", "info"), service="payment-gateway")


async def serve():
    try:
        import grpc
        from cockpit.payment.v1 import payment_pb2_grpc
        from runtime.grpcio import aio_server, bind_port, run_aio_server
        from providers import resolve_payment_providers
        from server import PaymentGatewayServicer
        from store import PaymentStore
        from worker import PollWorker
    except ImportError as e:
        print(f"[payment-gateway] proto not generated, cannot start gRPC: {e}")
        print("[payment-gateway] Run 'make proto' first.")
        return

    store = PaymentStore()
    providers = resolve_payment_providers()   # 决议行 + 严格栈闸（mock 决议在 on 档拒启）

    nc = None
    nats_url = os.getenv("NATS_URL", "")
    if nats_url:
        try:
            import nats
            nc = await nats.connect(nats_url, max_reconnect_attempts=-1)
            print("[payment-gateway] NATS connected (payment_result 推送开启)", flush=True)
        except Exception as e:
            print(f"[payment-gateway] NATS 连接失败（回执推送禁用，不影响支付）：{e}",
                  flush=True)

    servicer = PaymentGatewayServicer(store, providers)
    worker = PollWorker(store, providers, audit=servicer._audit, nc=nc)
    worker_task = asyncio.create_task(worker.run(), name="payment-poll-worker")

    port = int(os.getenv("PAYMENT_PORT", "50071"))
    server = aio_server()
    payment_pb2_grpc.add_PaymentGatewayServicer_to_server(servicer, server)
    bind_port(server, f"[::]:{port}")
    await server.start()
    print(f"[payment-gateway] serving on :{port}", flush=True)
    try:
        await run_aio_server(server, name="payment-gateway")
    finally:
        worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except (asyncio.CancelledError, Exception):
            pass
        if nc is not None:
            try:
                await nc.drain()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(serve())
