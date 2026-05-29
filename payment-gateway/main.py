"""Payment Gateway 启动入口。"""
import asyncio
import os
import logging

logging.basicConfig(level=logging.INFO)

# proto 未生成时无法启动 gRPC，但 store 逻辑可独立测试


async def serve():
    try:
        import grpc
        from cockpit.payment.v1 import payment_pb2_grpc
        from server import PaymentGatewayServicer

        port = int(os.getenv("PAYMENT_PORT", "50071"))
        server = grpc.aio.server()
        payment_pb2_grpc.add_PaymentGatewayServicer_to_server(
            PaymentGatewayServicer(), server)
        server.add_insecure_port(f"[::]:{port}")
        await server.start()
        print(f"[payment-gateway] serving on :{port}", flush=True)
        await server.wait_for_termination()
    except ImportError as e:
        print(f"[payment-gateway] proto not generated, cannot start gRPC: {e}")
        print("[payment-gateway] Run 'make proto' first.")


if __name__ == "__main__":
    asyncio.run(serve())
