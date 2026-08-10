> **来源存档**：本文件是外部评审（ChatGPT，GPT-5.2 Thinking + GitHub 连接器）对本仓库的
> review 全文存档，原始分享链接 https://chatgpt.com/s/t_6a79dfbb751c8191b0d89bc2b6749e1b
> （2026-08-10 提取）。评审基于 commit cc87056（比提取时 HEAD ef5e28e 旧两个提交）。
> 原文中的 ChatGPT 内部引用标记（filecite）已清理，内容逐字未动。
> **采纳裁决见** ../2026-08-10-external-review-adoption.md ——本文件只是证据存档，
> 其中的判断不代表本项目结论；多处断言已被逐条核实，个别已过时（如 salvage A/B）。

# 最新仓库 Review 结论

本轮基于当前 `main` 最新提交 **`cc8705699e705c08747ce086350b669501f5f6af`** 重新评审。最新提交补齐了除雾能力面的对抗覆盖，并纠正了一次被 shell 管道吞掉退出码而产生的 `--strict` 假绿；对应 GitHub Actions CI #308 已完成且成功。当前 `main` 仍未启用分支保护，required checks 也没有强制。 

本轮重点复核了：

- 上次提出的执行安全旁路；
- 意图对抗评测的可信度；
- Planner 新的 `capability_ref` 协议；
- MiniMax 工具通道与 salvage 重试；
- 端侧能力扩充与数据飞轮；
- Registry、权限、mTLS 和量产安全边界；
- 当前 CI、主干治理和文档状态。

评审主要基于最新源码、提交记录、结构化评测报告和 CI 证据；本环境没有独立重跑整套 Docker 真栈和付费模型完整 gate，因此不会把仓库自报的模型读数冒充成本轮独立复测结果。

---

## 一、总体判定

| 评审对象 | 最新结论 |
|---|---|
| 作为 Phase 1 工程化 PoC | **通过，已是成熟度较高的平台型 PoC** |
| 核心架构方向 | **通过，不建议重写** |
| Agent / Skill / Exemplar 分层 | **通过，设计正确** |
| Planner 协议与意图评测 | **较上次有实质性提升，已经可以形成固定参考模型 baseline** |
| MiniMax 主模型质量 | **可继续作为主模型，但只能作为健康轨，不应强行追求正式 baseline** |
| 数据飞轮 | **机制已成型，但真实流量与 gold 供给仍不足** |
| 车辆执行安全 | **暂不通过真实车辆安全验收，仍有一个明确 P0** |
| T2 流式可靠性 | **存在一个明确 P1-high 重复执行风险** |
| 面向公网或生产部署 | **暂不通过，默认配置仍是 PoC fail-open 模式** |
| 当前最应该推进的方向 | **执行内核统一、VAL 确认权威、Capability Pack、生产安全基线** |

### 更新后的评分

| 维度 | 上次 | 本次 | 变化 |
|---|---:|---:|---|
| 总体架构 | 8.8 | **9.0** | 架构方向继续得到实现验证 |
| Agent / Skill 分层 | 8.6 | **8.8** | `capability_ref` 与资产职责更清楚 |
| Planner 与意图理解 | 6.8 | **8.5** | 协议、评测和诊断能力显著提升 |
| 数据飞轮 | 8.1 | **8.8** | 已能证伪错误修法，而不只是积累范例 |
| 测试与质量工程 | 7.4 | **8.6** | 上次对抗尺子的主要 P0 基本关闭 |
| 可维护性 | 7.5 | **7.4** | Planner 核心开始形成新的复杂状态机 |
| 安全架构设计 | 8.8 | **8.8** | 原则正确 |
| 安全实现闭环 | 6.8 | **6.2** | 仍有危险动作确认旁路，VAL 不是最终权威 |
| 量产安全与身份 | 5.8 | **5.9** | 有 mTLS/鉴权机制，但默认仍关且身份粒度不足 |
| Phase 1 PoC 综合 | 8.3 | **8.8，A** | 明显提升 |
| 真实车辆试点 | 暂缓 | **有条件暂缓** | 先关闭 N0 安全停止线 |
| 正式生产 | 不通过 | **不通过** | 仍需系统性硬化 |

---

# 二、与上次 Review 相比，哪些问题已经真正改善

## 1. 对抗评测已经从“发现工具”升级成了可审计的参考尺子

上次最大的判断之一是：对抗测试可以用于发现问题，但还不能形成正式 baseline。当前这一点已经有了实质性改变。

目前已有一份绑定固定代码、资产、Provider 和模型的 **DeepSeek 参考 baseline**：

- 总 gate：147/147；
- L0：25；
- L1：117；
- L2：4；
- L3：1；
- exact：121/121；
- raw 能力幻觉、校验后逃逸和不稳定均为 0/121；
- L1、L2 均使用两个独立进程，每个进程三次采样。

仓库也明确限制了它的解释范围：它只能证明 DeepSeek 在该固定快照下的意图理解和落域结果，不能证明 MiniMax、Agent 业务结果、外部 Provider 内容正确，也不能外推成跨模型平均质量。这个证据边界是正确的。 

上次指出的几个评测 P0，当前均已有机制化处理：

| 上次问题 | 当前状态 |
|---|---|
| `--case`、`--repeat 1` 可绕过正式 baseline | 参数面直接拒绝选集过滤器与自定义 repeat |
| 测试契约支持多轮，但只执行第一轮 | L2 已按 `case.turns` 顺序执行每一轮 |
| L2 丢失 Edge 副作用 | 已合并 Edge 与 Engine 两侧副作用 |
| L3 可能读取旧产物 | 已增加调用时间、Provider、选集、路径和产物身份校验 |
| raw Planner 输出与校验后结果混淆 | 已加入 raw candidate、pre-hint、validation 等截面 |
| 一条 case 即可覆盖正式 baseline | 正式写入要求完整 stable 声明集 |

相关代码已经显式限制 `--write-baseline` 的合法参数组合，并在多轮 L2 中合并 Edge 与 Engine 副作用。 

这意味着：

> **上次“评测体系暂不能作为正式 gate”的结论，现在可以修改为：DeepSeek 固定参考轨可以作为正式 baseline；MiniMax 主模型只能作为健康轨。**

---

## 2. Planner 的能力选择协议有了质变

当前 Planner 不再主要依赖模型直接输出裸 `agent_id + intent`，而是为每次请求生成不可变、请求级的 `PlannerCapabilityCatalog`，把本轮可见能力映射为：

```text
cap_0001
cap_0002
cap_0003
...
```

这个 Catalog 在权限过滤和预算裁剪之后生成，并成为该请求内唯一的能力权威；模型只能引用本轮可见的 opaque capability ref，Validator 再将其还原为真实 Agent 和 Intent。

这项改进有四个重要价值：

1. **模型不会因为记住旧 intent 名而调用当前请求不可见能力。**
2. **权限过滤后的能力才会拿到 ref。**
3. **raw 能力幻觉与校验后逃逸可以分开统计。**
4. **Catalog 裁剪、模型选择、Validator 接受结果使用同一请求级快照。**

这个设计比继续扩大 Prompt、补更多 Agent description 或加 route hint 更正确，也是本轮最值得肯定的架构升级之一。

---

## 3. 对 MiniMax 的问题已经做出了正确归因

当前证据表明，MiniMax 和 DeepSeek 的主要差异之一不是单纯“谁更聪明”，而是 **tool-calling 协议可靠性**：

- MiniMax 同批样本走成 toolcall 的比例大约 45%–48%；
- DeepSeek 同类样本达到 100%；
- 但在部分简单 stable 案例中，两者仍都能 20/20 通过；
- 真正的代价主要集中在需要填写 `complexity`、依赖和结构化字段的多阶段计划。

仓库因此没有简单地把 DeepSeek 147/147 解读成“DeepSeek 全面更准”，而是区分了：

- 可跨 Provider 比较的协议指标；
- 不应直接跨 Provider 比较的语义通过率。

这是正确的评测纪律。

当前已经实现：

- MiniMax salvage 后再次尝试工具通道；
- 第一次 salvage 计划保留为回落；
- 二次工具调用仍失败时，不会比不重试更差；
- 单列 `toolcall_salvage_kept` 进行观测；
- 开关 `PLANNER_TOOLCALL_SALVAGE_RETRY=on|off`。

但仓库也诚实地把它标记为“实现已完成，live A/B 尚未完成”，没有说成“已经修好”。这一点应继续保持。 

---

## 4. 数据飞轮已经具备“证伪错误修法”的能力

最新几轮改动里，有几项非常好的工程判断：

### 除雾能力

原问题不是描述不够好，而是能力面根本没有前后挡除雾能力。当前将其建设为：

```text
front_defogger.open
front_defogger.close
rear_defogger.open
rear_defogger.close
```

并补齐知识库、端侧能力、对象桥接与对抗覆盖。

### 穿衣指数

原有裸“指数”规则把生活指数判为股指。当前根据实际语料分布，将生活指数明确落入 `info.indices`，并收窄股票触发面。

### 裸对象澄清

项目分别尝试了：

- guide；
- clarify 型 exemplar；
- 调整候选池。

最终证据表明，“华润大厦”“上海”这类裸对象澄清是**输入形态和信息完整度问题**，不是内容检索问题：

- 裸专名之间几乎没有 lexical bigram 相似性；
- clarification exemplar 天然是完整请求的子串，容易抢走明确请求；
- 更多 guide 和 exemplar 反而可能产生误澄清。

因此仓库保留 clarify exemplar 机制，但刻意不投生产 clarify 数据。这个负结果非常有价值。

这说明数据飞轮已经开始回答：

> “这个修法是否真的有效”，而不只是“这个资产有没有被注入”。

---

# 三、当前仍然存在的关键问题

# P0：云端空结果降级仍可能绕过危险动作确认

这是当前最重要的问题，也是上次 Review 后仍未关闭的问题。

当前 Edge Orchestrator 的逻辑是：

1. 请求已上云；
2. 云端流正常结束；
3. 最终没有 speech；
4. 最终没有 actions；
5. Edge 再次执行 `classify_structured(request.text)`；
6. 直接调用 VAL；
7. 生成 `require_confirm=False` 的 Action。

源码中的 `CLOUD-DEGRADED-LOCAL` 分支没有调用 `_confirm_required()`。

而 `_confirm_required()` 明确知道以下对象需要确认：

- 后备箱；
- 门锁；
- 油箱盖；
- 充电口盖。



更严重的是，当前 VAL 对 `require_confirm` 的实现仍明确属于 PoC 行为：

```python
if self._need_confirm(obj):
    # PoC：直接执行；真实场景返回确认要求
    # 这里简化为标记后继续
```

也就是说，VAL 虽然知道它是危险动作，却不会自行拒绝执行。

这形成了完整旁路：

```text
用户：打开后备箱
        ↓
正常端侧入口发现危险 → 上云
        ↓
云端只发过程事件 / 空 final / Agent 流异常收束
        ↓
CLOUD-DEGRADED-LOCAL
        ↓
重新端侧分类
        ↓
直接 VAL.execute
        ↓
后备箱打开，require_confirm=False
```

### 风险等级

- 作为本地软件 PoC：高风险技术债；
- 作为真实车辆试点：**P0 阻断项**；
- 作为量产平台：绝对不可接受。

### 立即修法

第一阶段先封口：

```python
if local_structured and self._confirm_required(local_structured):
    yield NEED_CONFIRM
    return
```

同时：

- Cloud 已输出过任何 action 时，不允许再本地补执行；
- Cloud 已输出过有效 speech 时，不允许静默追加副作用；
- `CLOUD-DEGRADED-LOCAL` 只允许纯查询或明确标记 `effect=read` 的能力；
- 所有 write 能力必须重回统一执行链。

### 根治方案

VAL 必须接收绑定具体命令的 `ConfirmationGrant`：

```text
grant = {
  user_id,
  vehicle_id,
  session_id,
  capability_ref,
  canonical_payload_hash,
  nonce,
  issued_at,
  expires_at
}
```

危险命令进入 VAL 时：

```text
无有效 grant → NEED_CONFIRM
grant 与 payload 不匹配 → REJECT
grant 过期 → REJECT
grant 已消费 → REJECT
验证成功 → EXECUTE
```

这样未来即使上层又新增一条执行旁路，VAL 也不会执行。

---

# P1-high：T2 流式路径仍可能在部分输出后重新执行

T1 的 D0 流式路径做对了：

- 收到 speech，立刻将 `streamed=True`；
- 收到 action，也将 `streamed=True`；
- 如果没有 final，但已经输出过内容，则不再 unary fallback。



但 T2 的 LoopController 目前仍是：

```text
streamed = False

收到 speech:
    did_speak = True
    yield speech
    streamed 不变

收到 action:
    yield action
    streamed 不变

只有收到 final:
    streamed = True
```

然后：

```python
elif streamed:
    # Streamed speech but no final
```

这个分支实际上不可达，因为没有 final 时 `streamed` 仍是 False。随后 `if not streamed` 会进入 unary executor。

可能出现：

- 一句话播两次；
- Agent 调两次；
- 外部 API 调两次；
- action 已发出，连接中断后再次执行；
- 首次执行成功但 final 丢失，第二次形成重复副作用。

### 推荐修法

不要继续补一个 `streamed=True`，而应把 T1/T2 流式执行统一成一个组件：

```text
StreamState:
  NO_OUTPUT
  SPEECH_EMITTED
  ACTION_EMITTED
  FINAL_RECEIVED
  FAILED
  CANCELLED
```

fallback 规则：

| 当前状态 | 是否允许 unary fallback |
|---|---|
| `NO_OUTPUT` | 允许 |
| `SPEECH_EMITTED` | 不允许 |
| `ACTION_EMITTED` | 绝对不允许 |
| `FINAL_RECEIVED` | 不允许 |
| `CANCELLED` | 不允许 |

Action 已输出但没有 final，应进入：

```text
UNKNOWN_AFTER_SIDE_EFFECT
```

随后只能：

- 查询世界状态；
- 走 Outcome Verifier；
- 告知用户结果不确定；
- 绝不能透明重发。

---

# P1：L2 安全证据仍建议补“真实 VAL 命令探针”

目前 L2 已经合并：

```python
_engine_side_effects(engine)
+ runtime.edge_side_effect_rows(edge)
```

这关闭了上次“Edge 已执行、报告里却只有 Engine 副作用”的主要假绿。

但从当前 FullEntry 测试装配路径来看，L2 主要依赖：

- state delta；
- emitted action；
- Engine spy。

而 Edge runtime 已具备 VAL command probe 能力，但完整入口没有明显把它作为强制证据接入。我的判断基于静态路径复核，尚未在本轮执行反向突变。

建议增加一个专门的反向构造：

```text
让 VAL 真正执行危险命令
故意吞掉 action event
同时将状态恢复或屏蔽普通 state delta
L2 必须仍然因为 VAL command probe 而变红
```

对于普通质量评测，这是 P1；如果准备将 L2 用作真实车辆安全放行证据，则应按 P0-test 处理。

---

# P1：Planner 核心正在形成新的“布尔状态机”

当前 `planning.py` 的总体方向是正确的，但方法体已经同时处理：

- toolcall；
- toolcall salvage；
- tool retry；
- JSON fallback；
- clarify marker；
- focus continuity；
- open/close 极性；
- multi-action omission；
- adaptive consistency；
- no-action；
- directive addressed；
- explicit-input addressed retry；
- plan repair；
- route hint；
- fallback；
- capability validation。

其中存在多个互相影响的状态：

```text
retry_with_tool
salvage_kept
clarification_tool_retry
semantic_guard_retry
correction
no_action
last_mode
plan_mode
```



这些规则多数只是要求模型“重新回答”，而不是直接篡改计划，因此比 route hint 安全得多。但它们已经开始形成另一个难以推理的隐式状态机。

### 建议拆分为五层

```text
CatalogAuthority
      ↓
WireInvoker
      ↓
PlanParser
      ↓
PlanValidator
      ↓
RetryController
      ↓
FallbackPolicy
```

定义：

```python
@dataclass
class PlanAttemptState:
    attempt: int
    wire_mode: WireMode
    raw: str
    parsed: Plan | None
    validation_errors: list[ValidationError]
    fallback_candidate: Plan | None
```

每个 Retry Policy 只声明：

| 字段 | 含义 |
|---|---|
| `trigger` | 哪类验证错误触发 |
| `wire_modes` | 哪些通道适用 |
| `attempt_limit` | 最多重试次数 |
| `correction_template` | 给模型的反馈 |
| `risk_class` | 普通、语义、安全 |
| `metric_tag` | 观测归因 |
| `preserve_previous` | 是否保留前一次合法计划 |

这会避免继续在一个函数中增加 `elif` 和状态变量。

---

# P1：生产默认仍是 PoC fail-open

当前 Gateway：

- `AUTH_REQUIRED` 默认是 false；
- 无有效 token 时允许匿名；
- 匿名身份回落到默认 `u1/v1`；
- token 表仍是静态环境变量配置。



Cloud Planner 在没有 `granted_scopes` 时：

- 默认读取 `PERMISSIONS_FAIL_OPEN=true`；
- 自动注入 PoC 默认权限。



gRPC：

- `GRPC_TLS` 默认关闭；
- 开启后使用同一张 mesh 证书；
- 可以证明“调用方是 mesh 成员”，但无法区分具体服务身份。



权限主链也明确记录：

- 当前执行期只校验 granted scope；
- trust-level cap 尚未真正进入运行时决策主链。



Registry 的 `Register` 当前直接接受调用方提交的 Manifest 和 endpoint，Store 对同一个 `agent_id` 直接覆盖。当前 handler 中没有看到“客户端证书身份必须匹配 agent_id”的绑定。 

这意味着，在未来多租户或第三方 Agent 环境中，仅“打开 mTLS”还不够。一个拿到共享 mesh 证书的服务，理论上仍可能把自己注册成别的 Agent。

### 建议增加生产配置档

```text
DEPLOY_PROFILE=dev|test|demo|prod
```

`prod` 下必须满足：

| 配置 | prod 强制值 |
|---|---|
| `AUTH_REQUIRED` | true |
| `PERMISSIONS_FAIL_OPEN` | false |
| `GRPC_TLS` | on |
| `OBS_CONTENT_CAPTURE` | off |
| `REQUIRE_REAL_PROVIDERS` | on |
| 匿名身份 | 禁止 |
| 静态 AUTH_TOKENS | 禁止 |
| 默认数据库密码 | 禁止 |
| WebSocket Origin | 明确白名单 |
| 服务证书 | 每服务唯一身份 |
| Registry admission | 证书身份绑定 agent_id |
| trust cap | 父子 scope 感知后进入执行主链 |

任一项不满足时，服务拒绝启动。

---

# P1：新增车控能力仍需要人工同步太多资产

最新除雾能力的经历非常典型：

1. 增加 `front_defogger` / `rear_defogger`；
2. 增加 commands；
3. 增加 responses；
4. 增加 fast intent；
5. 增加 `VEHICLE_INTENTS`；
6. 增加 NLU object bridge；
7. 增加 Planner capability；
8. 增加 adversarial coverage。

第一次实现漏掉了对抗覆盖，`--strict` 实际退出 2，但人工通过 `| tail; echo $?` 又误报成 0，直到下一提交才补齐。

当前能力描述虽然已从 VAL 知识库自动生成，但 `VEHICLE_INTENTS` 仍是一份手工维护的集合。 

这说明：

> 新增能力依然不是一个原子动作，而是“同时记得修改八个位置”。

### 应升级为 Capability Pack

```text
capabilities/front_defogger.yaml
```

统一声明：

```yaml
id: front_defogger
operations:
  - open
  - close
aliases:
  - 前挡除雾
  - 前风挡除霜
effect: write
risk: low
confirmation: none
permissions:
  - vehicle.control
deployment:
  edge: true
  cloud_visible: true
verification:
  mode: state_match
nlu_equivalence:
  - 空调模式
coverage:
  required_families:
    - canonical
    - paraphrase
    - negation
    - object_flip
```

再由生成器产出：

- `VEHICLE_INTENTS`；
- LOCAL_INTENTS；
- Registry Manifest capabilities；
- NLU 对象桥接；
- capability description；
- coverage skeleton；
- 文档能力表；
- Validator schema。

CI 强制检查：

```text
任何 active capability
必须存在：
  执行定义
  权限定义
  风险定义
  覆盖定义
  验证定义或明确豁免
```

---

# P1：CI 没有直接执行新版 L0 strict 对抗门禁

最新提交已经证明，人工执行 `--strict` 时可以因为管道退出码误读而报告假绿。

当前 CI 的 `intent-eval-baseline` 主要运行：

- `eval_fast_intent.py`；
- `eval_route_hints.py`；
- `eval_registry_resolve.py`；
- Skill contract gate；
- Exemplar contract gate。

其中第一组还带 `continue-on-error`。当前 workflow 没有直接运行：

```bash
python test/eval_intent_adversarial.py \
  --suite discovery --layer l0 --strict

python test/eval_intent_adversarial.py \
  --suite gate --layer l0 --strict
```



虽然最新 CI 成功，但最能防止这次覆盖遗漏的门禁，还没有作为独立 blocking step 接进去。

### 建议

在 CI 中增加阻断步骤：

```yaml
- name: Intent adversarial L0 discovery gate
  run: python test/eval_intent_adversarial.py \
       --suite discovery --layer l0 --strict

- name: Intent adversarial stable gate
  run: python test/eval_intent_adversarial.py \
       --suite gate --layer l0 --strict
```

不要再通过 shell 管道读取退出码。最好统一封装：

```bash
make gate-intent-l0
```

或：

```bash
python scripts/check_intent_gate.py
```

由 Python 负责：

- 执行；
- 读取退出码；
- 生成 JSON；
- 输出 GitHub Annotation；
- 校验文档快照是否同步。

---

# P1：仓库治理不足以匹配当前项目复杂度

当前 `main`：

- 未启用 branch protection；
- required status checks 关闭；
- 最新提交未签名；
- 可以直接 push 到 main。



与此同时，仓库当前没有任何 open GitHub Issue，但 `AGENTS.md` 中仍存在 active、deferred 和条件待办。 

目前仅靠文档台账可以支持单人高强度研发，但随着项目复杂度增长，会出现：

- 待办缺乏 owner；
- 无法按风险排序；
- 设计文档与代码状态不同步；
- 直接 push 绕过 review；
- 安全文件和普通文档具有相同修改门槛；
- CI 绿但不是 required check。

### 建议立即启用

```text
main:
  require pull request
  require latest CI
  block force push
  block delete
  require resolved conversations
```

增加 CODEOWNERS：

```text
/orchestrator/edge/val.py
/orchestrator/edge/server.py
/security/
/registry/
/proto/
/orchestrator/cloud/planning.py
/test/eval_intent_adversarial.py
/docs/reviews/eval/baseline_*
```

安全与 baseline 文件至少需要一次独立 review。

---

# 四、对当前架构的最终判断

## 1. 不需要推翻 Multi-Agent 架构

目前项目的核心结构仍然成立：

```text
T0 端侧确定性快路径
T1 云端单次 DAG
T2 有界 Agentic Loop
Task Ledger 跨请求长任务
```

架构文档仍将其定义为 Phase 1 工程化 PoC，真实 SOME/IP/CAN、正式沙箱和量产部署属于目标态。

不建议改成：

- 单一大模型直接接管所有工具；
- 所有 Agent 互相自由群聊；
- LLM 直接发 CAN；
- 把所有 Agent 都变成 Skill；
- 用另一个 Agent Framework 重写现有运行时。

真正要做的是：

> **让所有执行路径最终汇聚到同一个不可绕过的执行安全内核。**

## 2. Agent、Skill、Exemplar 的分层继续保留

| 组件 | 最终职责 |
|---|---|
| Agent | 部署、状态、依赖、信任与权限边界 |
| Capability | 可执行的原子能力 |
| Skill Guide | 教 Planner 如何组合能力 |
| Skill Policy | 软规划约束 |
| Exemplar | 最软的历史案例提示 |
| Boundary | 人工裁定的跨域边界 |
| Workflow | 可恢复的多阶段流程模板 |
| VAL | 车辆动作的最终安全权威 |

因此，“是否应该把所有 Agent Skill 化”的答案仍然是：

> **不应该。应该把 Agent、Capability、Skill 和 Exemplar 打包交付，而不是互相替代。**

---

# 五、后续优化方向

## 方向一：统一执行生命周期

所有执行路径：

```text
T0 本地
mixed 本地部分
D0 流式
T1 DAG
T2 Loop
确认恢复
补槽恢复
MCP
主动服务
云端空结果降级
```

都必须经过：

```text
ADMIT
  ↓
BIND CAPABILITY
  ↓
AUTHORIZE
  ↓
CONFIRM
  ↓
IDEMPOTENCY CHECK
  ↓
DISPATCH
  ↓
VERIFY
  ↓
COMMIT
  ↓
OBSERVE
```

流式只决定“如何输出”，不能决定“是否跳过确认、幂等或验证”。

## 方向二：Capability Contract v2

当前 `Step.slots` 仍是 `dict[str, str]`，Action 和 Result 也主要是自由字典。

Manifest 的 Capability 仍主要声明 slot 名称，而没有强类型输入输出 schema。

建议增加：

| 字段 | 用途 |
|---|---|
| `input_schema` | 类型、required、枚举、范围、单位 |
| `output_schema` | 结构化结果契约 |
| `effect` | read / write / external-side-effect |
| `risk_level` | low / medium / high / critical |
| `confirmation` | none / explicit / strong |
| `replayable` | 是否允许透明重试 |
| `idempotency` | none / recommended / required |
| `compensation` | 是否支持撤销或补偿 |
| `verification` | readback / response / custom |
| `timeout_budget` | 延迟预算 |
| `context_scopes` | 最小上下文 |
| `version` | 契约版本 |
| `deprecation` | 退役与迁移策略 |

这份契约应同时驱动：

- Planner schema；
- Validator；
- Executor；
- VAL；
- HMI；
- MCP；
- Verifier；
- 对抗覆盖。

## 方向三：裸对象澄清改成“可执行性判定”

不要再为裸对象澄清增加 guide 或 exemplar。

建议单独建立：

```text
ActionabilityClassifier
```

输出：

```text
EXECUTE
CLARIFY
REJECT
```

输入特征包括：

- 是否有明确动作动词；
- 是否只有实体；
- 是否有对话焦点；
- 是否是省略续问；
- 必填槽位是否完整；
- 是否是显式输入或 hands-free；
- 是否有高风险对象；
- 是否存在唯一默认动作。

先 shadow 观察：

```text
actionability_decision
confidence
planner_decision
human_gold
```

获得真实分布后再 canary。

## 方向四：MiniMax salvage 重试做正式 live A/B

当前实现有了，但还没有验证收益。

A/B 必须按任务类型分层：

| 维度 | 需要统计 |
|---|---|
| toolcall 成功率 | off vs on |
| exact plan | simple / multi / adaptive |
| required group recall | 分层统计 |
| fallback rate | 是否下降 |
| `salvage_kept` | 是否频繁 |
| raw hallucination | 是否变化 |
| 高风险回归 | 必须为 0 |
| P50 / P95 | 延迟增量 |
| token / 调用次数 | 成本增量 |
| Provider 错误率 | 是否加剧限流 |

不要只看总通过率。最有可能获得收益的是：

- adaptive；
- 多步依赖；
- 需要 `complexity`；
- 需要 `slot_refs`；
- 需要结构化 clarify 的请求。

## 方向五：端侧 NLU 继续 shadow，暂不直接全量执行

当前 P3b 尚缺：

- operate 抽取；
- 真实错对象率分母；
- 真实流量；
- 执行侧对象化。

项目自己也明确：没有真实流量时，`<0.3%` 只有观测机制，没有实际分母。

放量顺序应为：

```text
shadow
  ↓
只读查询
  ↓
低风险、可回滚设置
  ↓
普通车控
  ↓
高风险对象始终保留确定性确认
```

早期绝不让模型直接自动执行：

- 后备箱；
- 门锁；
- 油箱盖；
- 充电口盖；
- 行驶受限动作；
- 支付与下单。

---

# 六、更新后的路线图

## N0：安全与证据停止线

### 目标

在继续扩业务能力前，关闭真实车辆不可接受的执行旁路。

### 工作项

| 工作项 | 优先级 |
|---|---|
| 修复 `CLOUD-DEGRADED-LOCAL` 危险动作确认绕过 | P0 |
| VAL 对危险动作强制要求 ConfirmationGrant | P0 |
| 修复 T2 partial stream 后 unary 重跑 | P1-high |
| D0/T2 共用 StreamExecutionAdapter | P1 |
| L2 安装真实 VAL command probe | P1 |
| CI 阻断执行 discovery/gate L0 strict | P1 |
| 启用 main branch protection | P1 |

### 完成判据

```text
危险动作无 grant 的 state delta = 0
云端空 final 不会触发危险本地执行
speech/action 已输出后不会重跑 Agent
L2 能抓住“VAL 已执行但 action 被吞”
当前 head 的 CI 与 L0 strict 均由 required check 证明
```

---

## N1：统一 Execution Kernel

### 核心对象

```python
ExecutionEnvelope:
    request_id
    turn_id
    step_id
    command_id
    capability_ref
    canonical_payload_hash
    effect
    risk
    auth_context
    confirmation_grant
    idempotency_key
    trace_context
```

### 统一中间件

```text
CapabilityResolver
PermissionGuard
ConfirmationGuard
IdempotencyGuard
Dispatcher
OutcomeVerifier
AuditRecorder
```

### 完成判据

- 所有 write action 均有 `command_id`；
- 进程重启后同 `command_id` 不重复执行；
- 源码扫描只允许 Execution Kernel 调用 VAL；
- 新增 D0/T2/MCP 路径不需要手工补五套安全 hook；
- 不确定副作用进入 readback，不透明重试。

---

## N2：Planner Protocol Hardening

### 工作项

1. 完成 salvage retry live A/B。
2. 将 PlanBuilder 重构成 typed attempt state machine。
3. 将 Retry Policy 从主函数拆出。
4. 保留 `capability_ref` 作为唯一模型能力身份。
5. 对当前 SHA 重跑：
   - DeepSeek reference track；
   - MiniMax health track。
6. 不追求 MiniMax `eligible=True`，只监测：
   - 不稳定率；
   - raw hallucination；
   - fallback；
   - plan mode；
   - 高风险 case。

### 完成判据

- Planner 主流程不再依赖十余个互相影响的布尔状态；
- 每种 Retry Policy 可单测、可消融、可独立观测；
- salvage A/B 有明确收益/成本裁决；
- 当前 SHA 有对应的完整主模型健康报告；
- 不挪用相邻 SHA 的旧数字。

---

## N3：生产安全与仓库治理

### 工作项

- `DEPLOY_PROFILE=prod`；
- fail-closed 权限；
- JWT/OIDC；
- 每服务唯一 mTLS 身份；
- Registry 证书身份绑定 agent_id；
- signed manifest 或 admission allowlist；
- trust-level cap 进入执行主链；
- WebSocket Origin 白名单；
- Secret Manager；
- 内容采集默认关闭；
- readiness / liveness 分离；
- SBOM、依赖扫描、SAST、镜像扫描；
- main branch protection；
- CODEOWNERS；
- 安全文件强制独立 review；
- Release tag 与 baseline 产物签名。

### 完成判据

任一不安全生产配置都会导致启动失败，而不是打印 warning 后继续运行。

---

## N4：端侧语义 NLU 与澄清

### 工作项

1. 完成 operate / value / position 抽取。
2. 建立 privacy-safe 真实流量统计。
3. 上线 ActionabilityClassifier shadow。
4. 只读与低风险能力小流量 canary。
5. 不再扩大普通语义 `fast_intent` 规则。
6. route hints 继续按跨 Provider 证据退役。
7. 高风险能力永久保留确定性确认和 VAL 硬门。

### 准入判据

| 指标 | 目标 |
|---|---:|
| wrong-object | <0.3% |
| 疑问句误执行 | 0 |
| 否定句误执行 | 0 |
| forbidden route | 0 |
| 高风险模型自动执行 | 0 |
| 操作极性错误 | 0 |
| shadow 样本量 | 达到可统计规模 |

---

## N5：Capability Pack 与平台化

### 目标

让“增加一个能力”成为一个原子、可生成、可验证的交付动作。

### Capability Pack 内容

```text
manifest
input/output schema
execution mapping
risk and confirmation
permissions
idempotency
verification
skills
exemplars
boundaries
adversarial cases
journeys
migration
```

### 后续平台能力

- WorkflowTemplate；
- 暂停、恢复和补偿；
- 任务版本迁移；
- Catalog 检索化；
- Provider QPS 统一协调；
- 请求合并和缓存；
- 任务中心；
- 多乘员隐私删除 saga；
- MCP 订单状态、取消和补偿生命周期；
- 第三方 Agent admission。

---

# 七、近期优先级排序

| 排名 | 事项 | 结论 |
|---:|---|---|
| 1 | 云空结果危险动作确认旁路 | **立即修，P0** |
| 2 | VAL ConfirmationGrant | **立即设计并下沉** |
| 3 | T2 partial stream 重跑 | **立即修，P1-high** |
| 4 | L0 strict 进入 required CI | **立即补** |
| 5 | main 分支保护与 CODEOWNERS | **立即开启** |
| 6 | FullEntry VAL command probe | **补安全证据** |
| 7 | salvage retry live A/B | **当前最明确的模型优化实验** |
| 8 | Planner RetryController 重构 | **防止新规则工厂** |
| 9 | Capability Pack v1 | **解决多处同步遗漏** |
| 10 | prod profile fail-closed | **量产前必做** |
| 11 | Registry 服务身份绑定 | **第三方生态前必做** |
| 12 | P3b 与 Actionability shadow | **有真实分母后推进** |

---

# 八、明确不建议继续做的事情

1. **不要为了 MiniMax 出正式 baseline 去修改 gate 案例集或放宽资格闸。** 当前资格闸拒绝不稳定读数，说明它在正确工作。

2. **不要再用更多 guide 或 exemplar 修裸对象澄清。** 已有证据表明这是形态和缺参问题，不是内容检索问题。

3. **不要继续给 Planner 主函数增加零散 `elif` 守卫。** 下一步应该抽 Retry Policy，而不是扩大隐式状态机。

4. **不要在执行安全未收口前继续扩第 15、第 16 个业务 Agent。** 当前真正的短板不是业务域数量。

5. **不要将 test count 当作安全证明。** 4600 多条测试证明工程覆盖较强，但无法替代危险动作不可绕过的结构性不变量。

6. **不要立刻让 live LLM gate 阻断每个 PR。** 先做定时健康轨和低成本抽样，处理好模型方差、凭证、成本和 Provider 限流后再升级。

7. **不要只打开共享 mTLS 证书就宣称完成服务身份安全。** 还需要证书身份与 agent_id、权限和 Registry admission 绑定。

---

# 九、最终 Review 结果

当前 `cockpit-agent` 已经从“功能很多的 Multi-Agent Demo”进一步演进为：

> **具有请求级能力权威、可审计意图评测、Provider 分账、数据飞轮、端云执行和真实 HMI 的智能座舱平台型 PoC。**

上次最严重的评测可信度问题已经大幅改善，DeepSeek 固定参考 baseline 可以使用；MiniMax 也已经被正确定位为主模型健康轨，不再为了绿灯扭曲 gate。

但当前项目还没有跨过“真实车辆安全平台”的门槛。阻碍它的不是落域准确率，也不是 Agent 数量，而是：

1. **危险动作最终确认权仍然没有下沉到 VAL；**
2. **存在云空结果本地补执行旁路；**
3. **T2 流式状态机可能在部分输出后重新执行；**
4. **生产身份和权限默认仍是 fail-open；**
5. **新增能力仍依赖多处人工同步；**
6. **主干治理与项目复杂度不匹配。**

因此更新后的正式裁决是：

| 阶段 | 裁决 |
|---|---|
| Phase 1 工程化 PoC | **通过，A 级** |
| 架构继续演进 | **批准，保留现有总体架构** |
| DeepSeek 固定参考 baseline | **通过** |
| MiniMax 主模型运行 | **可继续，按健康轨管理** |
| 真实车静态演示 | **关闭 P0 后有条件进入** |
| 真实车动态试点 | **暂缓** |
| 第三方 Agent 生态 | **暂缓 Registry 身份与 Capability Contract v2 完成** |
| 公网生产部署 | **不通过** |
| 下一主线 | **N0 安全停止线 → N1 统一执行内核 → N2 Planner 协议硬化** |

最核心的一句话是：

> **仓库已经不需要再证明“它是不是 Agent”；下一阶段要证明的是，无论智能层、流式层、降级层如何变化，任何一次真实副作用都只能通过同一个不可绕过、可确认、可幂等、可验证的执行内核。**