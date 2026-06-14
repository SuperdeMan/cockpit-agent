# 测试与验证

## 1. 端侧纯逻辑 smoke（无需 docker）
```bash
python test/smoke_edge.py
```
验证 Fast Intent 分类、模拟 VAL 安全门控、端侧执行链。**当前结果：13 passed, 0 failed。**

## 2. 全量测试（一条命令，无需 docker）
```bash
python -m pytest --import-mode=importlib -q
```
`conftest.py` 已配好 PYTHONPATH，`--import-mode=importlib` 解决 test_agent.py 重名。**当前结果：268 passed, 2 skipped。**

### 测试分布
| 模块 | 文件 | 覆盖 |
|---|---|---|
| 车控知识库 | `orchestrator/edge/tests/test_val_knowledge.py` (55) | YAML 加载、实体归一化、命令校验、安全门控、响应选择 |
| Fast Intent 扩展 | `orchestrator/edge/tests/test_fast_intent_extended.py` (46) | 新 pattern、结构化输出、旧格式兼容 |
| 多意图切分 | `orchestrator/edge/tests/test_multi_intent_split.py` (23) | 本地并行、云回退、单意图、边界 |
| 多意图 DAG | `orchestrator/cloud/tests/test_multi_intent.py` (4) | 并行车控、串行跨域、控制+播报、单意图透转 |
| 云引擎 | `orchestrator/cloud/tests/` (6 files) | 确认闭环、流式、上下文、规划、会话、执行器 |
| ASR 转码 | `llm-gateway/tests/test_transcode.py` (8) | wav 透传、webm 转码、回退 |
| Agent | `agents/*/tests/` | 各 Agent 契约测试 |
| ASR E2E | `test/test_asr_e2e.py` (4) | wav/webm/空音频/voices（需 API key，无 key 跳过） |

## 3. 端到端测试（需 docker compose 起全栈）
```bash
pip install websockets
python test/e2e_ws.py   # 每条链路用独立 WebSocket 连接，超时 60s
```

## PoC 验收清单
| # | 链路 | 输入 | 期望 |
|---|---|---|---|
| 1 | 车控快路径 | 打开空调26度 | 端侧秒回，返回 `vehicle.control` 动作 |
| 2 | 云端导航 | 附近的充电站 | Planner 路由到导航 Agent，NEED_SLOT 追问关键词 |
| 3 | 云端闲聊 | 讲个笑话 | Planner 路由到闲聊 Agent，流式回复 |
| 4 | 确认闭环 | 订川菜馆今晚7点两位 → 确认 | 点餐 Agent 返回结果 → 确认 → 完成下单 |
| 5 | 多意图 | 打开空调并播放音乐 | 端侧拆分两个意图并行执行，话术合成 |
| 6 | 结构化车控 | 打开座椅加热 / 解锁车门 / 氛围灯设为蓝色 | 端侧秒回，走知识库校验+话术 |
| 7 | ASR 转码 | POST /api/asr format=webm | ffmpeg 转码后正常返回文本 |

> 注：未配置 `LLM_API_KEY` 时 LLM Gateway 用 MockProvider，链路可跑通但复杂意图能力受限。
