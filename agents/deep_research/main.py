"""deep-research Agent 启动入口。"""
import asyncio

from agents._sdk import serve
from agents.deep_research.src.agent import DeepResearchAgent

if __name__ == "__main__":
    asyncio.run(serve(DeepResearchAgent()))
