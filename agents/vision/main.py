"""视觉入口 Agent 启动入口（M4 P4）。"""
import asyncio
from agents._sdk import serve
from agents.vision.src.agent import VisionAgent

if __name__ == "__main__":
    asyncio.run(serve(VisionAgent()))
