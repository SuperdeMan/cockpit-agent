"""充能规划 Agent 启动入口。"""
import asyncio
from agents._sdk import serve
from agents.charging_planner.src.agent import ChargingPlannerAgent

if __name__ == "__main__":
    asyncio.run(serve(ChargingPlannerAgent()))
