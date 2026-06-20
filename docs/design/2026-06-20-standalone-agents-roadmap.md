# 独立 Agent 扩展路线：充能规划 / 场景编排 / 天气路况安全助手 + 打通护栏

- **状态**：草案（2026-06-20）。给出三个新 Agent 的设计骨架与**与现有架构的打通契约**，确保后续接手者写完能力即可融入、不绕过架构。
- **交付对象**：后续开发者 / Agent。先读 §3「打通契约」，再看 §4 对应 Agent 骨架照做。
- **关联代码**：`agents/_sdk/`（BaseAgent/AgentClient/server）、`agents/navigation/`（leaf 范本）、`agents/trip_planner/`（sub-planner 范本）、`orchestrator/edge/val.py`（车控唯一出口）、`orchestrator/cloud/planning.py:119`（动态 catalog）
- **关联文档**：`CLAUDE.md` §3/§5、`AGENTS.md` §7（新增 Agent 最短路径）、`agents/_sdk/README.md`、`docs/architecture/detailed/ws6-*.md`、[`docs/guides/provider-integration.md`](../guides/provider-integration.md)

> **一句话**：新增 Agent **不改编排核心**——经注册中心被发现、经 Planner 动态 catalog 被路由。你只写 `agents/<name>/`，但必须满足 §3 的打通契约，尤其**车控只经 VAL、LLM 不直连车控、危险动作二次确认**三条红线。

---

## 1. 现状与证据：两种 Agent 原型已有范本

| 原型 | 范本 | 特征 |
|---|---|---|
| **Leaf（工具型）** | `agents/navigation/`、`agents/info/` | 自己干活（调 provider/知识库），不调别的 Agent。`handle()` 直接产 `AgentResult`。 |
| **Sub-planner（编排型）** | `agents/trip_planner/src/agent.py` | 经 `self.agents.call(...)` **协作下层 Agent**，再用 LLM 组织结果。形成 Planner→子规划者→工具 Agent 层级。 |

- 协作经受控 `AgentClient`（`agents/_sdk/agent_client.py`）：深度上限 `MAX_DEPTH=2`（`:25/:53`）、环检测（`:60`）、超时；`asyncio.gather(return_exceptions=True)` 做部分失败降级（trip_planner `:35-49`）。**⚠️ 护栏当前跨进程未生效、权限不放大未实现——落地更深的 sub-planner 前必读 [ws6 §4.4](../architecture/detailed/ws6-real-capabilities-and-agent-collaboration.md)。**
- 车控**只经 VAL**（`orchestrator/edge/val.py`）：Agent/LLM 只产「意图/动作」，确定性 Executor 经 VAL 权限校验后下发（`AGENTS.md` 铁律 1/2）。
- Planner 动态 catalog + 按 capabilities 校验（`planning.py:119/:201`）：注册即可路由，**无需改编排**。

## 2. 问题：跨域 Agent 最容易绕过架构

充能规划/场景编排/路况助手都**跨域**（车控+导航+信息+状态），新手容易：直接在 Agent 里操作车控（破坏「只经 VAL」）、用 LLM 直接下发动作、跳过危险动作确认、协作成环或越权。本文用「打通契约」把这些堵死。

## 3. 打通契约（每个新 Agent 必须满足——抄这张表）

1. **目录与注册**：按 `agents/<snake>/` 建（参考 navigation 结构），`manifest.yaml` 声明 `agent_id`(kebab)/capabilities/`requires_permissions`/`trust_level`/`deployment`；继承 `agents/_sdk.BaseAgent` 实现 `handle()`，**不重写 gRPC/注册**（SDK 已封装 `serve()`）。
2. **端口与约定**：从 `conventions.md` §5 取下一个空端口（当前从 **50068** 起），同步 `Dockerfile` `AGENT_PORT`、`deploy/docker-compose.yaml` 服务、`conventions.md` agent 表/意图表/端口表。
3. **能力发现**：意图命名 `<domain>.<action>`；Planner 动态 catalog 自动收录，**不在 planner 里硬编码**。
4. **车控红线**：要控车 → 产出 `action`（如 `AgentResult().action("vehicle.control", {...})`），交端侧 Executor 经 **VAL** 校验下发；**严禁** Agent 直接碰 CAN/SOME-IP 或自行执行车控。危险动作置 `require_confirm=true`，走二次确认闭环。
5. **协作（仅 sub-planner）**：经 `self.agents.call(agent_id, intent, slots, ctx)`；遵守 `MAX_DEPTH`/环检测/权限不放大（**注：这些护栏跨进程尚未生效、权限校验未实现，见 [ws6 §4.4](../architecture/detailed/ws6-real-capabilities-and-agent-collaboration.md)，落地深层协作前先补**）；被调若需被解析，确认 `agent_client.py:port_map` 或 `<AGENT_ID>_ENDPOINT` 可达。
6. **外部能力**：接真实 provider 一律走 [provider-integration 指南](../guides/provider-integration.md)（_sdk/http、工厂、降级、可观测、测试）。
7. **权限/安全**：`requires_permissions` 最小化；敏感数据（精确位置/支付/音视频）最小上云；内容经 `security/` 审核。
8. **测试**：契约测试（参考 `agents/<x>/tests`）+ 黄金用例；改端侧跑 `smoke_edge.py`；全量 `pytest` 绿。

## 4. 三个 Agent 设计骨架（决策已定，字段/实现留执行者）

### 4.1 充能规划 `charging-planner`（原型：**Sub-planner**）
- **定位**：找充电站→排序（距离/电量/路线）→给规划，必要时叫导航。本质同 trip-planner，照抄其协作骨架。
- **意图**：`charging.plan`（slots: `destination?`/`soc?`/`prefer?`）。`deployment: cloud`，`trust: first_party`。
- **协作**：`self.agents.call("navigation","navigation.search_poi",{"keyword":"充电站",...})` 取站点；读电量/续航走 `ctx.fetch("vehicle.battery")` 等 scope（经 Memory，最小化）。可选 `info.weather`（低温续航）。
- **车控**：仅「导航去某站」产 `action(navigate)`，**不**直接控车。
- **scope**：`location.read`、`navigation.control`、`network.external`、电量相关只读 scope。
- **验收**：协作部分失败仍给规划（`gather(return_exceptions=True)`）；无导航 key 回退 mock 仍可跑。

### 4.2 场景编排 `scene-orchestrator`（原型：**Leaf，产多动作**；与 Planner 多意图分工）
- **定位**：把「回家模式/午休模式」等**命名场景**展开为一组确定性动作（车控+媒体+导航）。
- **与现有 Planner 的边界**（重要）：Planner 擅长**临时多意图**拆 DAG；scene-orchestrator 管**预定义命名场景**（稳定、可配置、可个性化）。用户说"开启回家模式"→Planner 路由到本 Agent→本 Agent 返回一组 `actions`。
- **意图**：`scene.activate`（slots: `scene`）。`deployment` 可 `cloud`（编排）或关键场景下沉 `edge`（低延迟）。
- **车控红线**：场景里的车控**只产 `action`**（空调/车窗/氛围灯…），由端侧 Executor 经 **VAL** 逐条校验+安全门控下发；危险项 `require_confirm`。场景定义放知识库/配置，不硬编码进编排核心。
- **scope**：按场景涉及域聚合（`vehicle.control`/`media.control`/`navigation.control`），最小化。
- **验收**：单场景展开的每个动作都过 VAL；行车中受限动作被安全门控拦截（复用 val 安全分支）。

### 4.3 天气路况安全助手 `road-safety`（原型：**Sub-planner / 响应式**，安全敏感）
- **定位**：综合天气(`info.weather`)+路况(导航/路况)+车辆状态→**主动安全提示**（雨雾限速、结冰、疲劳/远光提醒等）。
- **意图**：`safety.advise`（被动查询）；**主动播报**可由可观测/状态事件触发（订阅 NATS 车辆状态，节流）。`trust: first_party`。
- **协作**：`self.agents.call("info","info.weather",...)` + 导航路况 + `ctx.fetch` 车速/灯光等状态。
- **安全红线**：只**建议/提示**，**不自动控车**（如需"自动降速"必须 `require_confirm` 且经 VAL）；提示话术经审核，避免误导。安全敏感、低延迟项考虑端侧兜底。
- **scope**：`location.read`、`network.external`、车辆状态只读 scope。
- **验收**：天气/路况任一不可用仍给降级提示；主动播报有节流防打扰。

### 4.4 交易类 Agent 范式（被 [info-expansion](2026-06-20-info-agent-expansion.md) 的 `ticketing` 复用）
- `trust: third_party`，**强制经支付网关 + 二次确认**，Agent **不持支付凭证**。
- 时序：`Authorize(idempotency_key)`→返回 `payment_id`+`require_confirm`→`AgentResult(NEED_CONFIRM, action(require_confirm=true, payload{payment_id}))`→用户确认→`Capture(payment_id, confirm_token)`。见 `proto/cockpit/payment/v1/payment.proto`、`payment-gateway/`、ws6 §2。
- 范本：`agents/food_ordering/`、`agents/parking_payment/`。

## 5. 分阶段落地（建议）
- **P0 `charging-planner`**：最贴近现有 trip-planner，复用协作骨架，验证「sub-planner + 车辆状态 scope」链路。
- **P1 `scene-orchestrator`**：建场景知识库 + 多动作经 VAL，验证「Agent 产动作→VAL→执行」红线。
- **P2 `road-safety`**：引入状态事件触发 + 主动播报节流，验证「响应式 Agent + 安全提示」。

## 6. 验收（通用）
- 注册即被 Planner 路由（catalog 含其意图、校验通过），**未改编排核心**。
- 车控全部经 VAL（grep 该 Agent 无直接车控/CAN 调用）；危险动作走确认闭环。
- 协作守 `MAX_DEPTH`/环检测/权限不放大；部分失败降级。
- `pytest` 全绿 + 契约测试；改端侧 `smoke_edge.py` 13/13；`conventions.md` 三表更新。

## 7. 风险
- **车控绕过**：跨域 Agent 最大风险是图省事直接控车——评审重点查「是否只产 action、是否经 VAL」。
- **场景与多意图重叠**：scene-orchestrator 与 Planner 职责需清晰（命名场景 vs 临时多意图），否则路由混乱。
- **主动播报打扰**：road-safety 主动提示需节流 + 场景感知，避免频繁打断。
- **协作风暴**：sub-planner 叠加易超深度/成环——护栏已在 AgentClient，但新 Agent 勿自造绕过。
- **AgentClient endpoint 解析**：当前为 env/硬编码 port_map（`agent_client.py:_resolve_endpoint`，PoC），多实例/容器内需配 `<AGENT_ID>_ENDPOINT` 或演进为 registry 解析——被协作的新 Agent 务必确认可达。
