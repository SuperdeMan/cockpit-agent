# 外部评审采纳评估（2026-08-10）——逐条裁决与批次索引

> **状态**：已裁决（本文件即裁决记录；各批次方案见 §4 索引，落地进度见 `AGENTS.md` §4.1/§4.2）
> **评审来源**：ChatGPT（带 GitHub 连接器）对本仓库的全量 review，基于 commit `cc87056`。
> 全文存档：[`external/2026-08-10-gpt-repo-review.md`](external/2026-08-10-gpt-repo-review.md)。
> **裁决方法**：评审断言按「模型输出是不可信输入」纪律逐条对源码核实（关键断言核到 `文件:行号`），
> 再按项目当前阶段（Phase 1 PoC、无真实流量、单人研发）裁量采纳/降档/不采纳。

---

## 1. 一句话结论

评审的两个核心安全断言（P0 执行旁路、P1-high T2 流式重跑）**逐行核实为真**，且互为独立缺陷；
CI 缺口、能力同步痛点、治理缺口也属实。采纳为 **6 个批次（B1–B6）**：B1/B2 立即做（安全 + 门禁，
均为纯代码低风险），B3/B4 近期做（配置档 + 能力包），B5/B6 条件启动（重构与前瞻，写清启动条件）。
评审中的量产项（JWT/OIDC、SBOM、九段 Execution Kernel、完整 ConfirmationGrant 协议）**降档为
远期记录**——PoC 阶段无消费方，为做而做。已完成项（salvage A/B）不重复做。

## 2. 核实结果（评审断言 vs 源码证据）

| 评审断言 | 核实结论 | 证据 |
|---|---|---|
| P0：云端空结果降级绕过危险动作确认 | **真**。兜底分支重新分类后直接执行 VAL，无 `_confirm_required` 检查，构造 `require_confirm=False` | `orchestrator/edge/server.py:806-828`；对照正常路径 `server.py:708` 有闸 |
| P0 的第二半：VAL 不自行拒绝危险动作 | **真**。`_need_confirm` 命中时「PoC：直接执行」，注释原文如此 | `orchestrator/edge/val.py:212-215` |
| P1-high：T2 部分输出后 unary 重跑 | **真，且根因比评审说的更具体**：`elif streamed:` 分支不可达——`streamed` 唯一置 True 的位置在 `if final_sr is not None` 内部，final 丢失时必掉进 `if not streamed` 完整重跑。从注释意图看是把 `did_speak` 误写成 `streamed` | `orchestrator/cloud/loop.py:184,217,255,263`；对照 D0 正确实现 `orchestrator/cloud/engine.py:415,420` |
| 评审未提、核实中发现的同族缺口 | 兜底判定只看 `final.speech`，**云端流式 `speech_delta` 不计入**「云端已有输出」——云端已流出话术、final 空时边侧仍会本地补执行 | `orchestrator/edge/server.py:787-794`（循环只在 `which=="final"` 时更新 `cloud_speech`） |
| CI 没有阻断执行 L0 strict 对抗门禁 | **真**。`ci.yml` 无任何 `eval_intent_adversarial` 调用；`eval_fast_intent` 步骤还带 `continue-on-error: true` | `.github/workflows/ci.yml:214-230` |
| 生产默认 fail-open | **真，但定性要修正**：这是 R3.1/R3.2 设计过的「默认关、演示翻开」显式开关，不是无意识裸奔。真正缺的是「prod 档强制 fail-closed、不满足拒绝启动」 | `.env.example:206-249`（`AUTH_REQUIRED=false` / `PERMISSIONS_FAIL_OPEN=true` / `GRPC_TLS=off` 及设计注释） |
| Registry 注册无身份绑定，同 id 直接覆盖 | **真**（R3.1 是会话静态 token 两层鉴权，未做证书身份↔agent_id 绑定；PoC 单租户威胁模型下可接受，第三方生态前必须补） | `registry/` Register handler；设计见 `docs/design/2026-07-02-r3.1-session-auth.md` |
| 新增车控能力要人工同步约 8 处 | **真，且是刚发生的亲历**：除雾那批漏了对抗覆盖，`--strict` exit 2 被管道吞成假绿 | `docs/agents-history.md` §23.1；`cc87056` 提交本身 |
| L2 缺 VAL command probe 强制证据 | **部分真**：probe 机制已存在且有测试消费，缺的是把它接为 L2 完整入口的强制红灯源 | `test/support/intent_adversarial_runtime.py:118,157,177`（`install_val_probe`/`val_commands` 已在）|
| Planner 主函数形成布尔状态机 | **真**（`retry_with_tool`/`salvage_kept`/`clarification_tool_retry`/`semantic_guard_retry` 等多状态互扰，是我们自己 findings §24–§26 工作的对象） | `orchestrator/cloud/planning.py` |
| salvage retry「实现完成、live A/B 未完成」 | **已过时**。评审基于 `cc87056`；`f02a815`（2026-08-10 晚）已完成双臂 live A/B：51.3%→85.5%，+34.2pp，p=2.3e-08，默认 on | `docs/design/2026-08-02-intent-routing-adversarial-findings.md` §26.5 |
| `main` 无分支保护、可直接 push | **真**（当前工作方式即直接 push main） | GitHub 仓库设置（评审经 API 查证；与本地工作流一致） |

**确认闭环现状**（B1 设计的关键输入，评审没展开）：云端主链的确认闭环落点在
`orchestrator/edge/edge_call.py:268-274`——`meta.confirmed=="true"` 才放行，否则 NEED_CONFIRM
挂起；即**仓库里已存在「confirmed 凭据传递」模式**，VAL fail-closed 可直接复用该模式下沉，
不需要发明新协议。`val.execute` 生产调用点共 5 处（`server.py:363,885,887`、`edge_call.py:277`、
`edge_agents_mod/vehicle.py:64`+`media.py:24`），下沉后全部自动被兜住。

## 3. 逐条裁决

### 3.1 采纳（有行动，进批次）

| # | 评审建议 | 裁决 | 批次 |
|---|---|---|---|
| 1 | 修复 CLOUD-DEGRADED-LOCAL 危险动作确认绕过（P0） | **采纳，最高优先级**。修法较评审增强：危险对象直接不兜底（本地无确认闭环承接，评审的 `yield NEED_CONFIRM` 在此分支没有恢复通道）；`speech_delta`/action 已流出也禁兜底 | B1 |
| 2 | VAL 对危险动作 fail-closed（评审：ConfirmationGrant） | **采纳思想、降档实现**：`val.execute` 加 `confirmed` 参数（复用 `edge_call.py:268` 既有模式），危险对象未确认返回拒绝。完整 grant 协议（nonce/expiry/consume/payload-hash）记为真实 VAL 对接前置项——PoC 单进程内存 VAL 上做完整防重放协议是过度设计 | B1 |
| 3 | 修复 T2 partial stream 后 unary 重跑（P1-high） | **采纳**。修判定 bug + action 已发出时进「结果不确定」处置（不透明重跑）+ 补流断三场景测试 | B1 |
| 4 | L2 接入真实 VAL command probe 作强制证据 | **采纳**。probe 已在，工作量是把 `val_commands` 接为 L0–L2 完整入口断言面 + 一个反向突变探针 case | B1 |
| 5 | CI 阻断执行 discovery/gate L0 `--strict` | **采纳**。L0 全离线零网络零成本，完全符合「live LLM gate 不阻断 PR」的既有边界；用 Python 封装脚本读退出码（正是 2026-08-10 管道吞码坑的机制化根治） | B2 |
| 6 | main 分支保护 + CODEOWNERS | **采纳、分档给泓舟选**：轻档（block force-push/delete + required check 不强制 PR）不改变现有直推工作流；重档（require PR）改变日常节奏，列利弊待拍板。CODEOWNERS 先落文件（单人仓库下作用=安全敏感文件清单的机器可读化） | B2 |
| 7 | `DEPLOY_PROFILE=dev\|demo\|prod`，prod 强制 fail-closed 否则拒绝启动 | **采纳**。纯增量（dev 档逐字保持现状）；prod 强制表按评审清单裁剪至当前有实现承载的项 | B3 |
| 8 | Registry admission：身份绑定 agent_id | **采纳静态版**：注册 token→agent_id 允许表（env 配置，缺省关）。证书级绑定（每服务唯一证书）留到第三方生态前 | B3 |
| 9 | Capability Pack：能力单一声明源 + 生成器 + CI 完整性检查 | **采纳，分两步**：CI 能力完整性检查先行（不依赖格式统一，先把「漏一处」变红灯）；pack 格式增量迁移（新能力先走，存量渐进） | B4 |
| 10 | Capability Contract v2（effect/risk/confirmation/idempotency/schema…） | **部分采纳**：`effect`/`risk`/`confirmation` 三字段并入 B4 的 pack 格式（它们直接服务安全判定）；`input_schema`/`output_schema`/`compensation` 等完整契约记远期 | B4（三字段）|
| 11 | Planner 拆五层 + RetryPolicy 声明化 | **降档采纳**：只做 RetryPolicy 表驱动收敛（每条 trigger/attempt_limit/metric_tag 可单测可消融），不做五层大拆——planning.py 无正确性缺陷，1184 条测试钉着的核心不为可维护性单独大动。**条件启动**：下次要加新重试规则时先做这个 | B5 |
| 12 | D0/T2 共用流式执行组件（StreamExecutionAdapter） | **降档采纳**：抽共享的流态判定纯函数 + 状态枚举（NO_OUTPUT/SPEECH/ACTION/FINAL），D0/T2 共用 fallback 判定表；不建九段 Kernel。**条件启动**：下次新增流式路径前必做；B1 先用最小 diff 修 bug | B5 |
| 13 | ActionabilityClassifier（裸对象澄清换「可执行性判定」形态） | **采纳为预留的第四条路设计蓝图**。与 §4.2 既有结论完全一致（「下一次要动它得换判定形态，不是再加一层检索式知识」）。shadow 先行；**启动条件**：有真实流量分母，或该族 badcase 再次成为主要矛盾 | B6 |
| 14 | 端侧 NLU 放量顺序（shadow→只读→低风险→普通车控；高危永久确定性确认） | **采纳为原则条款**，并入 P3b 权威入口的放量条件；无当前行动（无真实流量） | B6 |
| 15 | 「执行安全未收口前不扩第 15/16 个业务 Agent」 | **采纳为阶段性冻结令**，写入 AGENTS.md §4.1（B1 完成即解除） | 即时生效 |

### 3.2 不采纳 / 已完成 / 维持现状

| 评审建议 | 裁决 | 理由 |
|---|---|---|
| salvage retry 正式 live A/B（评审方向四，10 维分层统计） | **已完成，不补跑** | `f02a815` 已收口：gate L1 双臂 117 样本，51.3%→85.5%（p=2.3e-08），重试成功率 ≈70%，墙钟 +38.5%，案例级验证（多日行程修复、注入不再兜底）、无回归，默认 on 已拍板。评审要求的 P50/P95、token 细账、provider 错误率属完美主义维度，A/B 结论已可行动 |
| 对当前 SHA 重跑 DeepSeek 参考轨 + MiniMax 健康轨 | **不单独发起** | §4.3 已有纪律：不为拿读数发起跑批。B1 落地后代码必然变化，届时如需引用主模型总分再按运行手册跑完整父 bundle（B1 文档的验收清单里有此提示） |
| 九段 Execution Kernel（ADMIT→…→OBSERVE 统一中间件） | **不采纳为近期工程** | PoC 阶段过度设计。其核心不变量（危险动作唯一权威在 VAL、write 不透明重试）用 B1 的下沉 + 源码级断言测试实现，成本 1/20 |
| 完整 ConfirmationGrant（nonce/expiry/consume/payload-hash） | **降档**（见 3.1#2） | 防重放的威胁模型要到真实多端/真实 VAL 才成立；PoC 信任边界内 `confirmed` 参数 + fail-closed 已关闭结构性风险 |
| JWT/OIDC、Secret Manager、SBOM/SAST/镜像扫描、每服务唯一 mTLS 证书 | **记录为量产远期，不排期** | 无真实用户、无公网面、单人研发；B3 的 prod 档校验会把这些列为 `prod-target` 注释项，避免遗忘 |
| Workflow 模板/任务中心/隐私删除 saga/MCP 生命周期/第三方 admission 等 N5 平台化 | **维持 §4.2 既有后置裁决** | 与 M-B/M-C/M-D、M2/M3 产品化边界的既有后置条款重合，不重复立项 |
| 「不要为 MiniMax 改 gate 案例集」「不要再用 guide/exemplar 修裸对象澄清」「不要 live gate 阻断 PR」等不做清单 | **确认一致，无新动作** | 均已是 §4.2/§4.3 成文纪律；评审独立得出相同结论可视为交叉验证 |

### 3.3 评审判断与本项目结论的两处修正

1. **评审建议旁路封口用 `yield NEED_CONFIRM`**——不对。端侧确认闭环依赖云端挂起/恢复
   （`_confirm_required` 注释：「危险动作不走本地秒回——落到云端经 edge_call→NEED_CONFIRM
   闭环」）；CLOUD-DEGRADED-LOCAL 触发的前提恰是云端没给出结果，本地发 NEED_CONFIRM 无人承接。
   正确处置是危险对象**不兜底**，播报与云端不可达同款的降级话术（见 B1 §3.1）。
2. **评审把 fail-open 默认描述为风险本身**——修正为「缺 prod 强制档」。默认值是 R3.1/R3.2
   拍板过的 PoC 形态（`.env.example` 注释写明「默认关/演示翻开」），改默认会破坏日常开发；
   要补的是第四种形态：`prod` 档下不满足即拒绝启动（B3）。

## 4. 批次索引（每批一份方案文档，可独立接手）

| 批次 | 方案文档 | 性质 | 启动时机 |
|---|---|---|---|
| **B1 执行安全停止线** | [`../design/2026-08-10-b1-execution-safety-stopline.md`](../design/2026-08-10-b1-execution-safety-stopline.md) | P0+P1-high 修复 + 安全证据 | ✅ **2026-08-10 已实施合入**（4 提交，验收 6/6，冻结令已撤销；实施记录见方案文档 §7） |
| **B2 意图门禁进 CI 与主干治理** | [`../design/2026-08-10-b2-gate-ci-branch-governance.md`](../design/2026-08-10-b2-gate-ci-branch-governance.md) | CI 阻断 + 治理 | ✅ **2026-08-10 已实施合入**（方案 A 全部 + CODEOWNERS，红灯验证已做）；轻档分支保护 2026-08-11 完成并核验（方案 §6.2）。**B2 全部收口** |
| **B3 DEPLOY_PROFILE 生产配置档** | [`../design/2026-08-10-b3-deploy-profile-fail-closed.md`](../design/2026-08-10-b3-deploy-profile-fail-closed.md) | 配置纪律 | ✅ **2026-08-11 已实施合入**（3 提交；六处与方案的差异逐条给了理由，实施记录见方案文档 §6）|
| **B4 Capability Pack v1** | [`../design/2026-08-10-b4-capability-pack.md`](../design/2026-08-10-b4-capability-pack.md) | 能力交付原子化 | ✅ **2026-08-11 已实施合入**（4 提交；门禁首跑抓到 22 条真缺陷全部修掉，实施记录见方案文档 §6）|
| **B5 Planner 重试策略表 + 流式统一** | [`../design/2026-08-10-b5-planner-retry-stream-refactor.md`](../design/2026-08-10-b5-planner-retry-stream-refactor.md) | 可维护性重构 | **条件启动**（加新重试规则/新流式路径前必做） |
| **B6 可执行性判定与前瞻契约** | [`../design/2026-08-10-b6-actionability-forward.md`](../design/2026-08-10-b6-actionability-forward.md) | 智能层预留出口 | **条件启动**（真实流量 / 该族再成主要矛盾） |

依赖关系：B1 独立；B2 独立；B3 轻依赖 B1（prod 档校验项引用 B1 的新开关）；B4 独立但其
`effect/risk` 字段被 B5/B6 消费；B5 依赖 B1 落地（在修正后的代码上重构）；B6 独立。

⚠ **B4 落地时把 `risk` 从声明字段改成了派生函数**（`capability_meta.risk_of`），
理由是 B1 刚把「危险与否」收敛成 `require_confirm` 这一个权威、第二份声明会漂移，
而 risk 声明的唯一消费方 B6 尚未开工（先落=死字段）。**对 B5/B6 的影响：调函数而不是读字段**，
真到那时若发现派生规则不够用再加声明，那时它有真消费方。裁决表第 10 条「三字段并入 pack 格式」
据此修正为「`effect` 落声明、`risk` 落派生、`confirmation` 沿用既有 `require_confirm`」。

## 5. 对评分表的态度

评审给的分数（架构 9.0、PoC 综合 8.8/A 等）**只作参考不进台账**——它是单一外部模型基于
静态阅读的打分，与本项目「读数必须带证据链」的口径不同。有价值的是它的**方向排序**：
执行安全 > 门禁治理 > 配置纪律 > 能力平台化 > 重构 > 前瞻，本裁决的批次顺序与之一致。
