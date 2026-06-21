"""天气路况安全助手 Agent 启动入口。"""
import asyncio
from agents._sdk import serve
from agents.road_safety.src.agent import RoadSafetyAgent

if __name__ == "__main__":
    asyncio.run(serve(RoadSafetyAgent()))
