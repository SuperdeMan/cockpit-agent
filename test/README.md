# 测试与验证

## 1. 端侧纯逻辑 smoke（无需 docker）
```bash
python test/smoke_edge.py
```
验证 Fast Intent 分类、模拟 VAL 安全门控、端侧执行链。**当前结果：13 passed, 0 failed。**

## 2. 全量测试（一条命令，无需 docker）
```bash
python -m pytest test/ orchestrator/cloud/tests/ security/tests/ observability/tests/ agents/ --import-mode=importlib -q
```
`conftest.py` 已配好 PYTHONPATH，`--import-mode=importlib` 解决 test_agent.py 重名。**当前结果：118 passed。**

## 3. 端到端测试（需 make up 起全栈）
```bash
pip install websockets
python test/e2e_ws.py   # 每条链路用独立 WebSocket 连接，超时 60s
```

## PoC 验收清单
| # | 链路 | 输入 | 期望 |
|---|---|---|---|
| 1 | 车控快路径 | 打开空调26度 | 端侧秒回，返回 `vehicle.control` 动作 |
| 2 | 云端导航 | 附近的充电站 | Planner 路由到导航 Agent，NEED_SLOT 追问关键词 |
| 3 | 云端闲聊 | 讲个笑话 | Planner 路由到闲聊 Agent，正常回复 |
| 4 | 确认闭环 | 订川菜馆今晚7点两位 → 确认 | 点餐 Agent 返回结果 → 确认 → 完成下单 |
| 5 | 断网降级 | 停掉 cloud-* 后说"讲个笑话" | 返回降级提示；车控仍正常 |

> 注：未配置 `LLM_API_KEY` 时 LLM Gateway 用 MockProvider，链路可跑通但复杂意图能力受限。
