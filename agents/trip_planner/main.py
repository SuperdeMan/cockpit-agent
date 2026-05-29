"""trip-planner Agent 启动入口。"""
import asyncio

from agents._sdk import serve
from agents.trip_planner.src.agent import TripPlannerAgent

if __name__ == "__main__":
    asyncio.run(serve(TripPlannerAgent()))
