# 智能座舱 Multi-Agent 系统

云边协同的智能座舱 AI Agent 系统：端侧"快系统"秒回车控/媒体类确定指令并离线兜底，云侧"慢系统"用 LLM Planner 编排复杂、跨域、多轮意图；所有 Agent 经统一契约 + 注册中心即插即用。

- 架构设计（唯一真相源）：[`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)
- 工程规则：[`CLAUDE.md`](CLAUDE.md)

## 架构一览

```
HMI ─► Edge Gateway ─► Edge Orchestrator ─► Fast Intent
                                   │
              ┌────快意图(本地秒回)─┤
              ▼                    └──慢意图(上云)──► Cloud Gateway ─► Cloud Planner
        车控/媒体 Agent ─► VAL ─► 车                         │
                                                            ├─► Agent Registry(发现)
                                                            ├─► LLM Gateway(多模型)
                                                            ├─► Memory(上下文/画像)
                                                            └─► core/eco Agents(gRPC)
```

## 快速开始

前置：Docker Desktop（含 Docker Compose）；本地开发另需 Go 1.24+、Python 3.11+、Node 20+、buf。

```bash
cp .env.example .env          # 填入 LLM_API_KEY（MiMo/Anthropic）
make proto                    # 生成 gRPC 代码
make up                       # 起全栈（18 个容器）
# 打开 http://localhost:5173  访问座舱 HMI
make down
```

Windows（PowerShell，无 make）：

```powershell
Copy-Item .env.example .env
./scripts/gen-proto.ps1
docker compose -f deploy/docker-compose.yaml --env-file .env up --build
```

LLM 默认使用小米 MiMo API（`LLM_PROVIDER=xiaomimimo`），也支持 Anthropic。不配 key 自动走 MockProvider。

## 验证四条 PoC 链路
1. **车控快路径**：说"打开空调26度"、"打开座椅加热"、"解锁车门"、"氛围灯设为蓝色" → 端侧秒回（断网也可用）。
2. **云端导航**：说"附近的充电站" → 云端 Planner 路由到导航 Agent，追问关键词。
3. **云端闲聊**：说"讲个笑话" → 云端 Planner 路由到闲聊 Agent，流式回复。
4. **确认闭环**：说"订川菜馆今晚7点两位" → 点餐 Agent 返回结果 → "确认" → 完成下单。

**多意图**：说"打开空调并播放音乐" → 端侧拆分两个意图并行执行。

```bash
python test/e2e_ws.py   # E2E 自动验证（需全栈运行）
```

## 目录
见 `CLAUDE.md` §3。每个服务子目录都有自己的 README，说明职责、接口、依赖。

## 状态
Phase 1 全部验收标准达成（2026-06-14）：268 测试全绿、E2E 4 链路通过、车控 150 意图、多意图切分、ASR/TTS 全链路。详见 [`AGENTS.md`](AGENTS.md) §4。
