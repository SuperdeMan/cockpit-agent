"""chitchat Agent 启动入口。"""
import asyncio

from agents._sdk import serve
from agents.chitchat.src.agent import ChitchatAgent

if __name__ == "__main__":
    asyncio.run(serve(ChitchatAgent()))
