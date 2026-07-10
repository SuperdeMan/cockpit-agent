"""Payment Gateway 启动入口。"""
import asyncio
import os

# 结构化日志：stdout JSON 带 trace/session + obs.log 上报（badcase 按 trace 检索）
from observability import setup_structured_logging

setup_structured_logging(os.getenv("LOG_LEVEL", "info"), service="payment-gateway")

# proto 未生成时无法启动 gRPC，但 store 逻辑可独立测试


async def serve():
    try:
        import grpc
        from cockpit.payment.v1 import payment_pb2_grpc
        from runtime.grpcio import aio_server, bind_port, run_aio_server
        from server import PaymentGatewayServicer

        port = int(os.getenv("PAYMENT_PORT", "50071"))
        server = aio_server()
        payment_pb2_grpc.add_PaymentGatewayServicer_to_server(
            PaymentGatewayServicer(), server)
        bind_port(server, f"[::]:{port}")
        await server.start()
        print(f"[payment-gateway] serving on :{port}", flush=True)
        await run_aio_server(server, name="payment-gateway")
    except ImportError as e:
        print(f"[payment-gateway] proto not generated, cannot start gRPC: {e}")
        print("[payment-gateway] Run 'make proto' first.")


if __name__ == "__main__":
    asyncio.run(serve())
