"""parking-payment Agent 启动入口。"""
import asyncio

from agents._sdk import serve
from agents.parking_payment.src.agent import ParkingPaymentAgent

if __name__ == "__main__":
    asyncio.run(serve(ParkingPaymentAgent()))
