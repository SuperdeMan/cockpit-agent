"""nearby（周边发现）Agent 启动入口。"""
import asyncio

from agents._sdk import serve
from agents.nearby.src.agent import NearbyAgent

if __name__ == "__main__":
    asyncio.run(serve(NearbyAgent()))
