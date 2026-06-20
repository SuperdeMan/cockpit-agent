"""info Agent 启动入口。"""
import asyncio

from agents._sdk import serve
from agents.info.src.agent import InfoAgent

if __name__ == "__main__":
    asyncio.run(serve(InfoAgent()))
