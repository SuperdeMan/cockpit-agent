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

每条 `vehicle.control` 动作的 `command`（如 `hvac.set` / `ambient_light.set` / `seat.recline` /
`volume.set` / `fragrance.on`）+ `params` 会在**端侧**经 `orchestrator/edge/edge_call.py` 的
`action_to_structured` 翻成 VAL 结构化命令（object/operate），再走 VAL 完整流水线
（归一→校验→**安全门控**→执行）。新增场景动作时，`command` 须能被该翻译识别——即对应
VAL `knowledge/commands.yaml` 里的对象/操作（友好参数 `color/position/angle` 会自动归一；
VAL 不认的舒适标签 mode 如 `auto/quiet` 会被丢弃）；否则该动作会落 legacy 串路径而无法执行。

## 设计文档

`docs/design/2026-06-20-standalone-agents-roadmap.md` §3.2、§8（命令对齐 VAL 的闭环记录）
