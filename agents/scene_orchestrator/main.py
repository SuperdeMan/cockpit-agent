"""场景编排 Agent 启动入口。"""
import asyncio
from agents._sdk import serve
from agents.scene_orchestrator.src.agent import SceneOrchestratorAgent

if __name__ == "__main__":
    asyncio.run(serve(SceneOrchestratorAgent()))
