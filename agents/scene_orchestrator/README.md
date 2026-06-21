# scene-orchestrator Agent

场景编排助手——把「回家模式/午休模式/露营模式」等命名场景展开为一组确定性动作。

## 能力

| intent | 说明 | 槽位 |
|---|---|---|
| `scene.activate` | 激活预定义场景模式 | scene, custom_params |
| `scene.deactivate` | 退出当前场景模式 | scene |
| `scene.list` | 列出可用场景 | — |

## 端口

50069

## 运行

```bash
# 单服务调试
AGENT_PORT=50069 python agents/scene_orchestrator/main.py

# Docker
docker compose up -d scene-orchestrator-agent
```

## 测试

```bash
python -m pytest agents/scene_orchestrator/tests/ -v --import-mode=importlib
```

## 场景知识库

`scenes.yaml` 定义预置场景（go_home / camping / nap / romance）。新增场景直接编辑此文件。

## 设计文档

`docs/design/2026-06-20-standalone-agents-roadmap.md` §3.2
