"""navigation Agent 启动入口。"""
import asyncio

from agents._sdk import serve
from agents.navigation.src.agent import NavigationAgent

if __name__ == "__main__":
    asyncio.run(serve(NavigationAgent()))
