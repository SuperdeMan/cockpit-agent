"""受控 MCP 桥启动入口。

两件事的顺序都不能反：
1. **日志先于 bootstrap**：准入决议（拒载/忽略/准入了哪些工具）是本 Agent 最该被审计的
   输出，而 `serve()` 里才配结构化日志——不提前配，这些 INFO 会被默认级别吞掉。
2. **bootstrap 先于 serve**：注册发生在 serve 里，capability 是从准入清单合成的，
   晚一步注册中心看到的就是空能力表。
"""
import asyncio
import os

from agents._sdk import serve
from agents.mcp_bridge.src.agent import McpBridgeAgent


async def main():
    try:
        from observability import setup_structured_logging
        setup_structured_logging(os.getenv("LOG_LEVEL", "info"), service="mcp-bridge")
    except Exception:
        pass
    agent = McpBridgeAgent()
    await agent.bootstrap()
    try:
        await serve(agent)
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
