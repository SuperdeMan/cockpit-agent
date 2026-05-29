# 测试与验证

## 1. 端侧纯逻辑 smoke（无需 docker，已验证通过）
```bash
python test/smoke_edge.py
```
验证 Fast Intent 分类、模拟 VAL 安全门控、端侧执行链。**当前结果：13 passed, 0 failed。**

## 2. 各服务契约/单元测试（需先 make proto）
```bash
make proto   # 生成 gen/python
PYTHONPATH=$PWD:$PWD/gen/python python -m pytest agents -q
```

## 3. 端到端三链路（需 make up 起全栈）
```bash
pip install websockets
python test/e2e_ws.py
```

## PoC 验收清单
| # | 链路 | 输入 | 期望 |
|---|---|---|---|
| 1 | 车控快路径 | 打开空调26度 | 端侧秒回，返回 `vehicle.control` 动作；断网仍可用 |
| 2 | 云端单 Agent | 附近的充电站 | Planner 路由到导航 Agent（配 LLM_API_KEY 后能抽槽返回 POI 卡片） |
| 3 | 云端兜底 | 讲个笑话 | Planner 路由到闲聊 Agent（配 key 后真实回复，否则 mock 回显） |
| 4 | 断网降级 | 停掉 cloud-* 后说"讲个笑话" | 返回降级提示；车控仍正常 |

> 注：未配置 `LLM_API_KEY` 时 LLM Gateway 用 MockProvider，链路可跑通但复杂意图能力受限（无法抽槽/真实对话）。
