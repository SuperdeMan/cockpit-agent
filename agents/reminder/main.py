"""reminder Agent 启动入口。"""
import asyncio

from agents._sdk import serve
from agents.reminder.src.agent import ReminderAgent

if __name__ == "__main__":
    asyncio.run(serve(ReminderAgent()))
