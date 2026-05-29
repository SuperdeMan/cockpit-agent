"""manual-rag Agent 启动入口。"""
import asyncio

from agents._sdk import serve
from agents.manual_rag.src.agent import ManualRagAgent

if __name__ == "__main__":
    asyncio.run(serve(ManualRagAgent()))
