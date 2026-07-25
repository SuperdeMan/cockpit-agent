# M3 子 RFC：统一主动引擎 + 受控 MCP 桥

> 日期：2026-07-25
> 状态：**设计待审**（§9 三个决策点待泓舟拍板）
> 依据：母提案 `2026-07-24-eva-benchmark-intelligence-upgrade.md` §4.E / §4.F / §6-M3
> 前序：M0a（确认兜底闸）/ M0b（Skill 层）/ M1a（submit_plan）/ M1b（自进化+Shadow NLU）/ M2（Ledger+Verifier+记忆图谱）全部收口
> 范围：**M3 两件事**——统一主动引擎（四路→六路主动收敛 + reminder P1b 位置触发）与受控 MCP 桥
> （人工准入 + 一读一写首批 + 写操作生命周期强制项）

---

## 0. TL;DR

1. **主动侧今天没有「治理」这一层**：六个生产方各自直发 `agent.proactive`，网关无条件广播给 HMI。
   节流是**各自进程内的、口径不一的**（road-safety 30/60 分钟、scene 30 分钟、reminder 明确不节流、
   memory/briefing/deep-research 完全不节流），跨生产方**零协调**——三个 Agent 同时想说话就响三次。
   免打扰时段、驾驶负荷门控、同类合并**一个都不存在**。
2. **落点：新增端侧基础设施服务 `proactive/`**（不是 Agent、不进注册中心路由）。
   生产方改发 `agent.proactive.request`，治理器裁决后发既有 `agent.proactive`——
   **下游（Go 网关 / HMI）零改动**，上游每个生产方改一行发布调用。
3. **fail-open 是硬要求**：发布走 NATS request/ack，**没人 ack（治理器没起/被关）就直发老主题**——
   治理器故障时行为**逐字回落到今天**，绝不静默吞掉用户显式约定的提醒。
4. **治理器零 kind 字面量**：优先级/情境断言/去重键/TTL 全由**生产方在信封里声明**，
   中央只实现通用策略（M2 Verifier「防长成下一个 fast_intent.py」的同款铁律，用源码断言测试钉死）。
5. **reminder P1b 位置触发**补断链（「到公司提醒我拿文件」），它同时是主动引擎的第七个生产方。
6. **受控 MCP 桥**：一个 `mcp-bridge` Agent 承载 stdio MCP server，**allowlist + 版本锁定 + 人工准入**，
   tool→capability 的映射是**声明式**的（不改编排核心）；首批一读一写，写操作五项生命周期
   （幂等键/订单状态机/timeout·cancel/补偿/审计）**缺一不接**。

---

## 1. 现状与接缝（2026-07-25 侦查，file:line）

### 1.1 主动侧

| 生产方 | 位置 | 现状节流 | 语义 |
|---|---|---|---|
| routine 建议 | `memory/server.py:18,108-121`（`_emit_proactive`） | **无** | 习惯沉淀后的一句建议 |
| 天气安全播报 | `agents/road_safety/src/agent.py:27,103-140` | 进程内 30 分钟 / 夜间 60 分钟，按 category | 安全类，开车时正该说 |
| 场景触发建议卡 | `agents/scene_orchestrator/src/{triggers.py,state_mirror.py:27,94-103}` | 进程内 30 分钟 + **边沿触发** | 建议卡，零执行权（D6） |
| 提醒到点 | `agents/reminder/src/{agent.py:25,69-75, scheduler.py:29-66}` | **明确不节流**（到点必响契约）+ 同批合并 | 用户显式约定 |
| 异步深调研完成/失败/截停 | `agents/deep_research/src/agent.py:362-414`（三处 publish） | **无** | 用户等着的结果 |
| 晨间早报 | `agents/info/src/handlers/briefing.py:66-90` | 每日一次（业务判据） | 环境类 |

- 消费方**只有一个**：`gateway/edge/main.go:366-386` 订阅 `agent.proactive` → `hub.broadcast`
  取 `speech/type/agent_id/card` 四个键 → HMI `App.tsx:425` 渲染 + `audio.ts:622` 排队朗读。
  → **输出契约收口在这四个键**，治理器只要产出同形状消息，下游一行不用改。
- 车况来源：`orchestrator/edge/main.py` 周期全量快照 + 变更 diff 发 `vehicle.state.changed`；
  键有 `speed_kmh / gear / battery / location / cabin_temp`（`orchestrator/edge/val.py:47-51`）。
  `agents/scene_orchestrator/src/state_mirror.py` 是**进程内建镜像的现成范式**（一条订阅多消费方、
  冷启动 ≤ 一个快照周期、全程 fail-open）——治理器照此建自己的镜像。
- 三态求值器 `agents/scene_orchestrator/src/solve.py:33-58`（`_cmp`：SAT/UNSAT/**UNKNOWN 绝不当满足**）——
  治理器的「情境断言」求值搬这两条设计（不跨包 import，见 §2.4）。

### 1.2 MCP 侧

| 现状 | 含义 |
|---|---|
| 全仓 **零 MCP 代码**（grep 只命中 info 的 `search_any` 里一个无关词） | 全新建，无历史包袱 |
| `trust_level` 三档 + 硬上限表 `security/scopes.py:36-52`；`third_party` 已禁高危车控/精确位置/摄像头麦克风 | MCP 桥用 `third_party` **不需要新建权限模型** |
| `nearby` / `parking-payment` 已是 `third_party` 先例 | manifest 形态可照抄 |
| payment-gateway 已有 `Authorize/Capture` + `idempotency_key` + `require_confirm`（`payment-gateway/server.py:20-45`） | **写操作的支付段不自建**，走既有边界（Agent 不持凭证） |
| M2 `agents/_sdk/ledger.py` + `task_ledger` 表（幂等键/状态机/心跳/cancel/orphaned 全在） | **MCP 写操作的订单状态机不另起炉灶**——是 Ledger 的第二个载体 |
| `_sdk/provenance.py` `_prov` 数据真实性标记（conventions §9.3） | 演示商户的诚实标注挂这里 |

---

## 2. 统一主动引擎

### 2.1 定位与边界

一句话：**「该不该现在打扰驾驶员」的唯一裁决点。**

- **是**：跨生产方的全局治理（频控 / 免打扰 / 驾驶负荷 / 同类去重 / 合并成一条 / 攒着说）。
- **不是**：内容生成器。治理器**不改写事实、不调 LLM**——合并只做确定性拼接
  （「A。另外，B」），卡片合并成 `card_group`（HMI 已支持，`Cards.tsx:103`）。
  让 LLM 重写合并话术是 v2，且届时必须限定「只重述不新增事实」。
- **不是**：执行器。沿用场景触发的 D6 底线——**主动路径零执行权**，产物只有话术 + 建议卡。
- **落点：独立服务 `proactive/`**（顶层目录，与 `registry/`/`memory/` 同级的基础设施服务）。
  否决的两个替代方案：
  - *折进 edge-gateway（Go）*：它确实是唯一消费点且已持车况镜像，但三态求值 + 合并窗口 + 延后队列
    要在 Go 里重写一遍，而我们全部测试资产在 pytest；且把定时缓冲塞进转发热路径，故障面变大。
  - *折进 edge-orchestrator（Python）*：它持有 VAL 权威车况（零镜像延迟），架构上「端侧管打扰」也成立——
    但它是安全关键的快路径主体，为一个可降级的治理特性引入定时器 + 待发队列，
    收益不抵爆炸半径。**独立服务的关键好处是它可以随时死掉**（§2.5 fail-open）。
- **部署位诚实说明**：治理器语义上属**端侧**（离线也该管住打扰）；PoC 单栈单 NATS，
  云侧生产方（deep-research/info/scene）与它同总线。真实端云分离时需要 NATS 桥接，
  与今天 `agent.proactive` 跨端云的处境完全相同，本 RFC 不引入新的跨域假设。

### 2.2 契约：请求信封与输出

**入（新主题 `agent.proactive.request`）** = 今天的 payload **原样** + 一组**全可选**的治理键：

```jsonc
{
  // ── 既有键，一字不改 ──
  "type": "reminder_fired", "speech": "...", "card": {...},
  "agent_id": "reminder", "ts": 1753400000000, "user_id": "u1",

  // ── 治理键（全可选；缺省 = 最保守但不静默）──
  "priority": "critical | user_contract | advisory | ambient",  // 缺省 advisory
  "conditions": [{"key": "battery", "op": "lt", "value": 20}],  // 投递时刻复核，三态
  "dedup_key": "charging.low-battery",                          // 缺省 = agent_id + type
  "ttl_ms": 300000                                              // 攒着说的最长等待，缺省 0 = 不延后
}
```

> **`merge_group` 已从设计中删除**：合并规则是「同窗到达即合并」，分组键没有消费方——
> 按 M2 沉淀的「消费面先于存储面」，没有消费方的字段不建。

**出（既有主题 `agent.proactive`）**：

- **单条通过 → 去掉治理键后原样转发**。这是兼容性的硬保证：网关与 HMI 看到的字节
  与今天逐字一致，单条路径不存在「新引入的行为差异」。
- **多条合并 → 一条新消息**：`type` 取最高优先级那条的 `type`；`speech` 按优先级序拼
  「A。另外，B」；`card` 单张直挂、多张合成 `card_group`；追加 `merged_from:[{agent_id,type}…]`
  （网关不读，dashboard/e2e 读）。

**裁决事件（新主题 `obs.proactive.decision`，best-effort）**：
`{request_id, agent_id, type, priority, decision: delivered|merged|deferred|dropped, reason, ts}`。
给 e2e 做确定性断言、给 dashboard 留后续视图；没人订阅也无副作用。

### 2.3 治理规则

**优先级四档（生产方声明，治理器不猜）**：

| 档 | 含义 | 免打扰 | 负荷门控 | 频控 | 合并窗口 |
|---|---|---|---|---|---|
| `critical` | 安全播报（危险天气/路况） | 豁免 | 豁免 | 豁免（仍计数） | 0ms（立即，且带走待发队列） |
| `user_contract` | 用户显式约定（提醒到点、等着的调研结果） | 豁免 | 豁免 | 豁免（仍计数） | 标准窗口 |
| `advisory` | 建议类（场景触发、routine、顺路充电） | 抑制 | 抑制→延后 | 受限 | 标准窗口 |
| `ambient` | 环境类（早报） | 抑制 | 抑制→延后 | 受限 | 标准窗口 |

**六道闸（顺序即优先级）**：

1. **情境断言复核**（`conditions`）：对治理器的车况镜像做三态求值。
   **UNSAT 或 UNKNOWN 一律丢弃**——生产方声称「电量 18%」，投递时刻要么证实、要么别说。
   这条闸顺带解决了「产出时成立、延后 5 分钟后已不成立」的陈旧建议问题。
   *（无 `conditions` 的消息不过此闸——不是所有播报都有可验证的前提。）*
2. **同类去重**（`dedup_key`）：窗口内（默认 600s）同键只过第一条，后到的丢弃。
   **跨生产方生效**——这是今天各自进程内节流做不到的那一半。
3. **免打扰时段**（`PROACTIVE_QUIET_HOURS`，业务时区 UTC+8 同 `BUSINESS_TZ`）：抑制 advisory/ambient。
   **默认空 = 该闸不启用**（§9-3 拍板：车机夜间场景与手机不同，机制建好但不预设口径）。
4. **驾驶负荷门控**：`speed_kmh >= 阈值`（默认 80）判为高负荷 → advisory/ambient **延后**（不丢）。
   **镜像读不到车速（UNKNOWN）→ 放行**。这一条是本 RFC 唯一**故意背离**「unknown 不打扰」的地方，
   理由写在这里备查：镜像冷启动最长 30 秒全空，若 UNKNOWN 判抑制，则治理器每次重启后的头 30 秒
   会静默吞掉所有主动消息——**「读不到车速」不是「用户在忙」的证据**，用它定罪是拿缺数据当事实。
   （对比闸 1：那里 UNKNOWN 丢弃是因为**生产方自己声称**有个前提，无法证实就不该替它说。）
5. **全局频控**：滚动 1 小时窗口，默认 6 条上限；advisory/ambient 超限即丢（记 obs），
   critical/user_contract 豁免但计数（这样一次密集的安全播报之后，建议类会自然安静下来）。
6. **合并窗口**：第一条进入待发队列时开窗（默认 1500ms）；窗口内到达的全部合并成一条。
   `critical` 窗口为 0——安全播报不等人，并且**顺带把待发队列一起冲出去**（避免安全播报刚说完
   1 秒后又单独响一条建议）。

**延后队列**：被闸 4 抑制且 `ttl_ms > 0` 的进延后队列，每 `PROACTIVE_DEFER_TICK_S`（默认 5s）复评——
车速降下来 / 免打扰结束就进待发队列；TTL 到期即丢并记 obs。`ttl_ms=0`（缺省）= 现在说不了就算了，
**不做无限期堆积**（堆到明天再说的建议不如不说）。

**零领域字面量铁律**：治理器源码里**不得出现任何 `agent_id` / `type` 的具体值**
（不得有 `if type == "reminder_fired"` 这类分支）。用 M2 Verifier 同款**源码断言测试**钉死：
扫描 `proactive/*.py` 断言不含既有六个生产方的 agent_id 与 type 字面量。

### 2.4 三态求值器：抄设计不抄代码

治理器需要三态比较，`agents/scene_orchestrator/src/solve.py` 已有一份。**不跨包 import**——
scene 的求值器绑着 catalog/derive_assert 的场景语义，且 Agent 包不该被基础设施服务反向依赖。
治理器自带一个 ~40 行的 `evaluate(cond, env) -> sat|unsat|unknown`，
**行为对齐由契约测试保证**（同一组用例跑两边，结果必须一致）。
> 这是刻意的重复：两处各自 40 行、语义靠测试对齐，好过为消除重复建一个谁都要依赖的公共包。

### 2.5 fail-open：ack 或直发

共享客户端 `runtime/proactive.py`（`runtime/` 已被全部 Agent 与 memory 的 Dockerfile COPY，
是唯一天然的共享落点；CLAUDE.md §3 该行描述随本卡更新为「共享运行时（gRPC + 主动事件出口）」）：

```python
async def publish_proactive(nc, payload: dict, *, timeout_s: float = 0.5) -> str:
    """治理器在 → 交给它；治理器不在 → 直发老主题。返回 'governed' | 'direct' | 'skipped'。"""
    try:
        await nc.request(GOVERNOR_SUBJECT, _enc(payload), timeout=timeout_s)
        return "governed"
    except (NoRespondersError, TimeoutError):     # 服务没起 / 被关 / 卡住
        await nc.publish(LEGACY_SUBJECT, _enc(payload))
        return "direct"
```

- 治理器**收到即 ack**（ack = 投递所有权移交），合并/延后在 ack 之后异步做——生产方不被窗口阻塞。
- NATS 的 no-responders 让「服务没起」是**毫秒级失败**，不是 500ms 超时。
- 关掉治理器（`PROACTIVE_GOVERNOR_ENABLED=false` 或停容器）→ 全部生产方自动回落**今天的行为**。
  这就是一键回退。

### 2.6 生产方迁移（七路）

| 生产方 | 改动 | 声明 |
|---|---|---|
| road-safety | 换发布调用；**保留**自身 30/60 分钟节流（生产侧防抖 + 中央治理是两层，不互斥） | `priority: critical` |
| reminder（到点） | 换发布调用 | `user_contract`，`dedup_key: reminder.<id>` |
| **reminder（位置，新）** | §3 | `user_contract` |
| scene 触发 | 换发布调用（`state_mirror.publish`） | `advisory`，`conditions` 带上触发条件本身，`ttl_ms: 300000` |
| memory routine | 换发布调用 | `advisory`，`ttl_ms: 600000` |
| deep-research（完成/失败/截停） | 换发布调用 | `user_contract`（用户在等） |
| info 早报 | 换发布调用 | `ambient`，`ttl_ms: 1800000` |

**防回归**：契约测试断言全仓（除 `runtime/proactive.py` 与治理器自身）**不再出现**
直发 `"agent.proactive"` 的字面量——迁移漏一个就红。

### 2.7 DoD 场景怎么真的发生

母提案 DoD：「电量 18% + 导航回家途中 + 顺路有桩」→ **一条**合并建议。
今天没有任何生产方会因为低电量说话（road-safety 只看天气预警）。所以 P0 附带
**一个新生产方**：`charging-planner` 低电量顺路建议——
订 `vehicle.state.changed`，`battery` 跌破阈值（默认 20%）**边沿触发**，
查沿途桩（已有 `plan_route` 能力），发 `advisory` + `conditions:[battery<20]` + `dedup_key: charging.low-battery`。
它与 scene 的低电量触发（用户若建了省电场景）**同窗到达 → 合并成一条**，DoD 即为真。
> 这不是为了凑 DoD 加的功能：「电量低 + 顺路有桩」正是母提案 E5「服务找人」的样板场景，
> 且 charging-planner 的路线规划能力早就在，缺的只有主动出口。

---

## 3. reminder P1b：位置触发

**为什么它在 M3**：母提案 §4.E 明列它是主动侧的断链（ETA 型已通，缺真实到达事件驱动）；
它也是治理器的第七个生产方，正好验证「新生产方接入 = 声明 + 一行发布」。

- **数据模型**：`reminder_item` 加 `place_name TEXT`、`place_lat/place_lon DOUBLE PRECISION`、
  `radius_m INT DEFAULT 300`、`trigger_on TEXT DEFAULT 'arrive'`（arrive|leave）；`kind` 增第三态 `location`。
  schema.sql 幂等 `ALTER TABLE … ADD COLUMN IF NOT EXISTS`（reminder 建表本就每次启动执行）。
- **解析**：新 `placeparse.py`，与 `timeparse.py` 同层——「到公司提醒我拿文件」「到家了提醒我收快递」
  「离开机场提醒我打车」。**地点名不猜**：解析出名词后按序解析坐标
  ① 画像常用地点（`profile.places`，家/公司带 `lat`——`agents/navigation/src/agent.py:441` 已是同源判据）；
  ② 记忆关系边（M2 `ResolvePersonPlace`，「孩子学校」这类）；
  ③ `navigation.search_poi`（跨 Agent 调用，reminder 已有 `self.agents`）；
  ④ 全落空 → **诚实追问**「没找到这个地点，能说得更具体点吗」——绝不存一条永远不会触发的提醒。
- **触发**：reminder 建自己的车况镜像（同 §2.1 范式），`location` 变更时对 pending 的位置提醒求值：
  - 双方都有坐标 → haversine ≤ `radius_m`；
  - 否则名称匹配（`location.name/district/city` 命中 `place_name`）——PoC 的诚实下限，写在文档里。
  - **边沿触发**：只在「未到达 → 到达」的变沿发一次（scene triggers 同款，防 GPS 抖动风暴）。
- **PoC 边界（诚实留档）**：`location` 今天只能由 debug 通道注入（`orchestrator/edge/server.py:130`
  `_DEBUG_KEYS`），没有真实 GPS 流。road-safety 的「进入新区域」播报也是同一条路。
  真实车机接 GPS 后本模块零改动——它只消费 `vehicle.state.changed`。
- `context_scopes` 补 `location`。

---

## 4. 受控 MCP 桥

### 4.1 定位与边界

- **一个 Agent 承载 N 个 MCP server**（`agents/mcp_bridge/`），不是「每个外部服务写一个 Agent」。
- **接入永远是人工准入**：server 与 tool 都要进 allowlist 才可见；**版本锁定**，
  server 版本或 tool schema 变了就**拒绝加载并告警**（不是自动接受）。
- **MCP 只做生态桥**：内部核心能力保持 gRPC 强类型低延迟，不迁 MCP（母提案既定，坚持）。
- 权限：`trust_level: third_party` + `network.external`；写操作 `require_confirm: true`
  （M0a 的中央确认闸会强制落实——这正是那一层防御纵深第一次接住外部能力）。

### 4.2 准入清单（声明式，不改编排核心）

```yaml
# agents/mcp_bridge/servers.yaml —— 唯一准入依据，改这个文件才能接新工具
servers:
  - id: demo-coffee
    transport: stdio
    command: ["python", "-m", "mcp_servers.demo_coffee"]
    version: "0.1.0"            # 与 server initialize 返回的版本**必须逐字相等**，否则拒载
    trust: third_party
    demo: true                  # 演示商户 → _prov 标记 + 卡片角标 + 首次下单话术明说
    tools:
      - name: menu.list         # MCP 侧 tool 名
        intent: shop.menu       # 映射成的 capability intent
        write: false
      - name: order.create
        intent: shop.order
        write: true
        require_confirm: true
        idempotent_key_arg: idempotency_key
```

启动时：`initialize` → `tools/list` → **与 allowlist 求交集**（多出来的工具直接忽略并记日志）→
校验版本与 tool schema 指纹 → 合成 capability 注册进 registry。
**新增工具 = 改 servers.yaml + 人工审，零编排核心改动**——与 route_hints/verification 同款哲学。

### 4.3 协议客户端

`agents/mcp_bridge/src/mcp_client.py`：JSON-RPC 2.0 over stdio，实现 `initialize` /
`tools/list` / `tools/call` 三个方法即可（本轮不做 resources/prompts/sampling）。
子进程生命周期、超时、崩溃重启、stderr 收集入日志。
**互操作验证**：首批虽是我们自己写的 server，但要对**第三方实现**做一次协议探针
（`@modelcontextprotocol/server-everything` 之类），确认握手/错误码/schema 形态不是自说自话；
网络或 Node 环境不可得则**如实记录未验证**，不假装验过。

### 4.4 写操作生命周期五项（母提案 §4.F 强制项，缺一不接）

| 项 | 做法 |
|---|---|
| 幂等键 | 请求侧生成（uuid4）并落 Ledger；同一逻辑订单重试**必须复用同键**。「连说两遍不双下单」= M2 幂等受理的第二个载体 |
| 订单状态机 | 复用 `task_ledger`（M2 已有 running/done/cancelled/orphaned 与心跳）：`created→confirmed→submitted→done`，异常分支 `failed/compensated`。**不新建表** |
| timeout / cancel | 调用超时进 `failed`；用户「取消订单」走 Ledger 的拉模式 cancel（M2 既有），桥再调 server 的取消工具（若 allowlist 声明了） |
| 补偿 | 已扣款但履约失败 → 走 payment-gateway 退款路径；**没有退款工具的写操作不许标 `write: true`** |
| 审计 | 每次 tools/call 记 `obs` span（server/tool/参数摘要/结果码/耗时），参数按 `observability/redact.py` 既有脱敏口径处理 |

### 4.5 首批两工具与诚实标注

首批载体是**我们自己写的演示商户 MCP server**（`mcp_servers/demo_coffee/`，母提案 §4.F 原文
「示例咖啡下单 mock→真实」即此意）：`menu.list`（读）+ `order.create`（写，全套生命周期）。
**诚实标注是硬要求**（铁律③ 的同一条精神）：`demo: true` 的 server 产出的卡片
一律打 `_prov = demo`、HMI 卡片带「演示商户」角标、下单确认话术明说是演示——
**演示不是问题，把演示装成真实才是**。

---

## 5. 契约登记与观测

- `docs/conventions.md` 新增：§9.8 主动消息信封与治理契约（主题名/优先级四档/治理键/裁决事件）、
  §9.9 MCP 准入清单契约（servers.yaml 字段与版本锁定语义）。
- 端口：治理器不开 gRPC（纯 NATS 消费者），只留 `/healthz`（HTTP，端口登记进 conventions 速查表）。
- obs：`obs.proactive.decision` 事件 + 结构化日志；dashboard 视图后置（非本卡 DoD）。

---

## 6. 分期与 DoD

| 期 | 交付 | DoD |
|---|---|---|
| **P0 主动治理器** | `proactive/` 服务 + `runtime/proactive.py` 客户端 + 六路生产方迁移 + charging 低电量生产方 | ① 单条路径输出与今天逐字一致；② **DoD 场景：低电量 + 场景触发同窗 → HMI 只响一条**；③ 治理器停掉 → 逐字回落今天行为；④ 零 kind 字面量源码断言绿 |
| **P1 reminder 位置触发** | placeparse + 坐标解析四级 + 镜像边沿触发 + `kind=location` | 「到公司提醒我拿文件」→ 注入 location 到达 → 触达一次且只一次；地点解析不出 → 诚实追问 |
| **P2 受控 MCP 桥** | mcp_client + servers.yaml 准入 + demo-coffee 一读一写 + 写生命周期五项 | ① 版本不符拒载；② allowlist 外的 tool 不出现在 capability；③ 下单确认链 CDP 绿；④ 连说两遍不双下单；⑤ 演示标注在卡片上可见 |

每期：pytest 增量 + e2e 脚本挂 `run_e2e` + journeys regression 不掉绿。

---

## 7. 不做清单（v1 边界）

- LLM 改写合并话术（v1 确定性拼接；改写必须「只重述不新增事实」，另立卡）。
- 多用户维度的免打扰/频控（`occupant_id` 恒 primary，无消费方）。
- 主动消息的用户偏好学习（「这类别再提醒我」→ 记忆反馈闭环）——有价值，但要先有治理器产生的数据。
- 治理器持久化（重启丢待发/延后队列）：这些消息的生命周期以秒计，落库不值当；
  **明确不做，写在这里备查**。
- MCP resources/prompts/sampling、HTTP/SSE transport、动态放行注册。
- 真实商户 BD（非技术依赖，母提案已标）。

---

## 8. 风险

| 风险 | 缓解 |
|---|---|
| 治理器成为主动链路单点 | fail-open ack 回落（§2.5）+ 契约测试覆盖「治理器缺席」路径 |
| 合并窗口引入 1.5s 延迟 | 主动播报无实时性要求；critical 窗口为 0；e2e 断言端到端时延 |
| 中央治理器长成第二个 fast_intent | 零 kind 字面量源码断言 + 优先级由生产方声明 |
| 生产方迁移漏一个 → 绕过治理 | 全仓字面量断言测试（§2.6）；漏的那个行为=今天，不是新故障 |
| MCP 子进程崩溃/挂死拖垮桥 Agent | 子进程隔离 + 超时 + 崩溃重启上限；桥自身健康与 server 健康分开上报 |
| 演示商户被误当真实能力 | `_prov=demo` + 卡片角标 + 话术明说，三处冗余 |

---

## 9. 决策点（2026-07-25 泓舟拍板）

1. **治理器落点** → **新增独立服务 `proactive/`**（按 §2.1 推荐；关键判据是「它必须可以随时死掉」）。
2. **M3 交付范围** → **P0+P1+P2 全做**（母提案原定三件）。
3. **免打扰时段** → **不设默认值**（`PROACTIVE_QUIET_HOURS` 默认空 = 该闸不启用）。
   理由：车机夜间场景与手机不同，夜里开车的人反而更需要提示；**机制建好但不预设口径**，
   等真实使用数据再定。§2.3 闸 3 的默认值随此改为空。

---

**附：与母提案的两处口径修正（记账）**

- 母提案 §4.E 说「四路主动」，实际是**六路**（deep-research 三处 + info 早报是 M1 之后新增的，
  提案盘点时口径没跟上）。迁移清单按六路 + 位置提醒 = 七路。
- 母提案 §4.E 说「判断规则复用 scene 的三态求值器」——**复用的是设计不是代码**（§2.4 理由）。
