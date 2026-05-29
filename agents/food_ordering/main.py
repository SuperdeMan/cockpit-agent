"""food-ordering Agent 启动入口。"""
import asyncio

from agents._sdk import serve
from agents.food_ordering.src.agent import FoodOrderingAgent

if __name__ == "__main__":
    asyncio.run(serve(FoodOrderingAgent()))
