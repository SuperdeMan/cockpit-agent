# proactive —— 统一主动引擎（M3 P0）

「该不该现在打扰驾驶员」的**唯一裁决点**。

```
生产方 --publish_proactive()--> agent.proactive.request
                                        │
                                  [proactive]  ←── vehicle.state.changed（车况镜像）
                                  六道闸 + 合并窗口 + 延后队列
                                        │
                                  agent.proactive  ← 契约不变
                                        │
                                edge-gateway(Go) ──> HMI   零改动
```

## 为什么要有它

治理器上线前，六个生产方各自直发 `agent.proactive`，网关无条件广播。节流是各自进程内的、
口径不一的（road-safety 30/60 分钟、scene 30 分钟、reminder 明确不节流、memory/早报/
深调研完全不节流），**跨生产方零协调**——三个 Agent 同时想说话就响三次。
免打扰时段、驾驶负荷门控、同类合并一个都不存在。

## 六道闸（顺序即优先级）

| # | 闸 | 判据 |
|---|---|---|
| 1 | 情境断言复核 | 生产方声明的 `conditions` 在**投递时刻**三态求值；`unsat`/`unknown` 一律丢 |
| 2 | 同类去重 | `dedup_key` 窗口内只过第一条，**跨生产方** |
| 3 | 免打扰时段 | `PROACTIVE_QUIET_HOURS`，**默认空=不启用** |
| 4 | 驾驶负荷 | `speed_kmh ≥ 阈值` → 建议/环境类**延后**；**读不到车速 = 放行** |
| 5 | 全局频控 | 滚动 1 小时窗口，按**投递消息数**计（合并因此天然省额度） |
| 6 | 合并窗口 | 同窗到达的多条 → 一条（`critical` 窗口为 0，且带走待发队列） |

优先级四档与信封字段见 `docs/conventions.md` §9.8。

## 三条不可动摇的性质

1. **它可以随时死掉**：生产方走 request/ack，没人 ack 就直发老主题。
   停容器 / `PROACTIVE_GOVERNOR_ENABLED=false` = 一键回退到治理器上线前。
2. **零 kind 字面量**：源码里不得出现任何生产方 agent_id / 消息 type。
   新增生产方 = 在信封里声明，不改这里一行（源码断言测试钉死）。
3. **不改写事实、零执行权**：合并只做确定性拼接，全程零 LLM；产物只有话术 + 建议卡。

## 文件

| 文件 | 职责 |
|---|---|
| `governor.py` | 六道闸 + 合并 + 延后队列（纯 asyncio，NATS 由 main 注入） |
| `evaluate.py` | 三态求值（与 `scene_orchestrator/src/solve.py` 语义靠契约测试对齐，刻意的重复实现） |
| `mirror.py` | 车况镜像（订 `vehicle.state.changed`，冷启动 ≤ 一个快照周期） |
| `main.py` | NATS 接线 + `/healthz`（默认 50075） |

## 本地跑

```bash
NATS_URL=nats://127.0.0.1:4222 python -m proactive.main
python -m pytest proactive -q --import-mode=importlib
```
