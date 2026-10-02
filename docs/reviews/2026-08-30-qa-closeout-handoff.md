# QA 轮当前交接：已闭合范围、生产证据与剩余活项

> 状态：**开发批与安全主链已闭合，QA 验收仍非全绿**
> 更新时间：2026-10-02（CA2-08 持久准入全部切片已部署：机制 `753c1a49` 惰性上线后，经用户授权以 `fec77afb` 上线 task_ledger 新列与演示商户 durable 声明，真栈并发双确认只下一单）；2026-09-28 CA2-07 权限视图记录保留
> 受众：接手 QA、Planner、Info、语音/TTS 或发布验证的人
> 历史流水：[`docs/agents-history.md`](../agents-history.md)（只追加，不在本页复述逐批过程）

## 1. 一句话结论

**QA 验收仍非全绿**。当前 release 为 `fa9cbe9eac406b355138b325f1f351d270653744`：CA2-15 S2 乘员只认声音证明、车机免唤醒语音没认出时只读普通偏好、S2S 只为 token 主体开会话，叠加在 S1b 删除与在途写入同代际失效（Memory owner 代际）之上，叠加在 S1a 记忆 / 声纹 HTTP 端点只按 Bearer 主体办事之上，再叠加在 CA2-11 车端操作日志与接收端幂等、超时后的恢复查询，叠加车端条件句整句上云的修复，叠加在 CA2-10 执行结果证据（回执 / 状态满足 / 观测归属 / 已核实分开）之上，
叠加在 CA2-09 挂起一次消费与确认绑定（含车端挡位复核）和 CA2-08 之上。CA2-08 执行方持久准入已启用——
task_ledger 新增 `operation` 列（经用户授权与发布闸一次性 schema 摘要上线），演示商户 `shop.order` / `shop.order_cancel`
先落准入记录再执行；见 [持久准入](../design/2026-10-01-v2-durable-operation-admission.md)。其下仍包含 CA2-07 的 Cloud/Agent 权限化上下文视图；主体权限、接收需求与在途读取失效机制已落地。
权限专项和固定语料已复验；记忆 / 声纹 HTTP 端点与 S2S `session.start` 已按 token 主体收口（CA2-15 S1a / S2），S2S 直接历史、collector 调试面与量产全局身份边界未关闭；车态仍是模拟来源。
发布、status、verify 与最新专项证据集中维护在本页 §2；AGENTS.md §4.0 只保留入口。

v2 与 Jev 按 [后续路线图](../roadmap.md)推进。前版 `89b19956` 固定语料三次复验为 101 测量轮，raw 6 红：
5 个业务问题与 1 个关键词误报；混合诉求手册展示为 8/9，有一次已派发后的 15 秒超时。
其中聚合改写已在 `0a589e14` 的 [原文保护](../design/2026-09-28-result-speech-fidelity.md)专项验证 3/3；
随后暴露的胎压条件遗漏及新分支原图缺失，由 [来源条件与图标护栏](../design/2026-09-28-manual-source-evidence-guard.md#5-受控图标补口)修复。
来源护栏批 `56409fef` 的文字专项 17/18：一轮规划重试丢掉手册步骤，到达手册的 17 轮条件均完整；图标专项 6/6。
这不代表所有手册生成都已安全正确。其他检索/指代/落域问题、
双端设备与声学未验面保留。Jev 尚未接入，不宣称完整 v2 或 QA 已签收。
已完成批次仍使用原 SHA，历史证据见 §3–4 和 [agents-history](../agents-history.md)。

## 2. 当前发布与证据边界

| 项目 | 最后登记事实 / 使用限制 |
|---|---|
| 源码与文档起点 | 研究采纳起点 `47c62b44d335a3c76da90f89f05fa2fc887c2742`；运行时首批版本见下行；后续纯文档提交允许领先 production |
| 生产 release | `b38e5afc1a6683416830199a31f2e7a6adda1816`，2026-10-02 激活：CA2-11，车端写能力 durable、SQLite 操作日志（命名卷 `car-agent-edge-operations`）、`EdgeCall.operation_query`、云端超时恢复；用户逐项批准车端 schema 摘要 `4103bc98…` 与基础设施锚 `3a24c39a…`，无 .env / CI/CD 变更。前一版 `9d6b05099a84eced72da1587b5b9b232b48b3d08`：车端遇到延后条件句（「如果…就…」「低于…时…」）整句上云、不再拆句就地执行，判据与云端规划器共用 `runtime/deferred_condition.py`；无 schema / .env / CI/CD 变更。前一版 `a772e783d10857d210bcb24619dae12def26e454`：CA2-10，声明了 state_match 的步带每次派发的观测关联键、车端只给本次改动的样本打标并回回执，ResultBundle 行新增 `evidence`；proto 只增字段，无 schema / .env / CI/CD 变更。前一版 `56c5519fcd664193e47572cc6cf1a25a33cfc0bb`：CA2-09，确认/补槽/澄清/取消先认领挂起、确认绑定车辆与最终参数、车端比对确认时的挡位；无 schema / .env / CI/CD 变更。前一版 `fec77afb3fa45a6e39e78e6cb0f5bf8ef4307ceb`：CA2-08 切片 D，task_ledger 加 `operation JSONB NOT NULL DEFAULT '{}'`（用户逐项授权，摘要 `55c23829…cdb23`）+ mcp-bridge 两项演示写工具 durable；.env/token/CI/CD 未改，未做数据迁移。前一版 `753c1a4903f1343e8c3853ec835b98c34b841c8f` 为 CA2-08 机制（惰性）+ 发布闸 schema 批准通道。前一版 `01cf47cd4455ea1912a4e61c16fe183e86d4d295`，2026-09-28 21:16 CST 激活；相对 `b095caca` 只改文档与 README（排除 `.md` 后零差异），应用代码与 `b095caca` 相同。`b095caca46a4a4188e2b26927327117c62326acb`（19:27 激活）是 CA2-07 Cloud/Agent 权限视图；再前一版 `55165e50` 为容量治理 P1、应用仍沿用 `7b346c90`。来源签名保持启用，.env/token/schema 未因这两版修改 |
| status / verify | `b38e5afc`：ok、5/5 healthy、零 warning，release/running SHA 一致；verify verified：`20261002T112108Z-b38e5af.json`，e2e_remote_safe / minimax:MiniMax-M3。`9d6b0509`：verify `20261002T012249Z-9d6b050.json`。`a772e783`：verify `20261001T184602Z-a772e78.json`。`56c5519f`：verify `20261001T172435Z-56c5519.json`。`fec77afb`：verify `20261001T164344Z-fec77af.json`。`753c1a49`：同样 5/5 零 warning，verify `20261001T161142Z-753c1a4.json`。前一版 `01cf47cd`：ok、5/5 healthy、零 warning，release/running SHA 一致（2026-09-29 复核）；统一 verify verified：`20260928T131738Z-01cf47c.json`，e2e_remote_safe / minimax:MiniMax-M3。`b095caca` 的 verify 为 `20260928T112855Z-b095cac.json`；CA2-07 专项与全量成绩属 `b095caca`，未对 `01cf47cd` 重跑 |
| CA2-06/12 后端验证 | `89b19956`：10148 passed / 34 skipped / 9 warnings，4727.69 s；四道门禁、edge smoke、Go 五包通过；有状态故障实验 16/16。工作树额外缺两项 NLU vocab，不混同主仓 32 skip 口径；[本包 §8](../design/2026-09-27-v2-vehicle-state-and-simulation.md#8-验证登记) |
| 前版后端与门禁（CA2-07） | `b095caca` 干净工作树全量 10309 passed / 35 skipped / 11 warnings，463.81 s；四门禁、smoke 与 [CI 8 项](https://github.com/SuperdeMan/cockpit-agent/actions/runs/36414421462) success。35 skip 按 Windows/POSIX/本地服务等条件登记，11 warning 为既有 Starlette/AsyncMock/Bert 弃用；实现提交 `0265bc40` 的 10293/34/11、567.24 s 单独留档，不转借 |
| 来源条件与图标专项（历史） | release/runner `56409fef`：文字六句 ×3 为 17/18，图标两句 ×3 为 6/6；已派发手册条件完整，6 份受控图标 hash 相同。共 35 次调用仅 Planner、均 pinned MiniMax-M3；零动作/确认/26 键车态差异/证据错误/残留挂起。不转写为 `7b346c90` 或整本新成绩 |
| 来源护栏批红轮（56409fef） | MSE01 r2 / `68e86285b5d84fab9be198a1baab2c36`：首个有效 salvage 有安全与手册两步，通道修复重试仍 salvage、只剩安全，覆盖首轮计划；无手册派发/卡。原策略有历史补槽收益，保留并登记到既有评估项，不关闭重试或用多步骤合并猜覆盖 |
| 前版来源条件专项 | `56f92838` 的六句 ×3 为 18/18，27 次 Planner 调用；随后发现图标原图丢失，已由 `56409fef` 修复。该前版成绩不转借后续版本 |
| 聚合原文保护专项 | release/runner `0a589e14`：V211 ×3，原文保留/推荐值/真实手册卡均 3/3，7 次调用均 MiniMax-M3 且没有聚合模型调用；零动作/确认/26 键车态变化/残留挂起。raw 3 PASS 中 r3 上游省略灯状态与复位前提，人工保留为安全内容问题；不报整题 3/3 验收 |
| 前版车辆身份只读专项 | release/runner `0a589e14`，15/15，HTTP/WS 隔离和错车拒绝保持；`.artifacts/vehicle-state-v2/0a589e14-live-readonly-retry.json`。首趟仅 status 前置未过，原 artifact 保留；之后独立 status 与完整重跑通过 |
| 前版挂确认结果专项 | release/runner `0a589e14`，V201–V203 ×3，9 case run / 21 测量轮；完整答案/卡与取消后结果引用各 9/9，零动作/车态变化/残留挂起。raw 21 PASS 中 V201 三次回忆回答均否认既有内容，人工记为残余；[逐条边界](../design/2026-09-28-result-speech-fidelity.md#51-挂确认结果回归与剩余回忆问题) |
| 历史手册专项（本轮未重跑全量） | `5a2f4c9d`：章节 187/187、视觉 35/35、胎压复合句 16/20（有手册步 16/16）、词法自信三句 8/9、调节类 6/6、口语语料 39/43 与 38/43（失败在规划层）、指代 8/9、空调模式 3/3、多步按话术 3/3、原 36 题可发送子集 31/31，车态零差异；[二批记录](../design/2026-09-26-manual-rag-colloquial-recall.md) §8 |
| 手册证据限制 | `.artifacts/manual-rag-colloquial/live-*-5a2f4c9d*.json` 属旧 release；5 条旧表述被预检拦下，不能报当前 36/36；当时挂确认无手册卡，后续 CA2-04 的 9 个样本闭环不改写旧结论 |
| 历史 QA 证据 | §3–4、T24/T47 等分别绑定自己的 release/provider，不转借当前 release；旧入口发布行见 [快照](../history/2026-09-26-entry-status-snapshot.md) |
| Android 包与验收 | [剩余待办总表](../design/2026-09-14-android-remaining-todos.md)记录设备与包身份；服务端 SHA、APK SHA、设备安装状态分列，本次未验包 |
| v2 小基线 | 前测 `d9970d9a` / runner `9aa5216b`，20×3 / 100 轮，raw 11 个红轮（9 个丢卡、1 个规划失败、1 个关键词误报）；后测同语料见下行，不混合分母 |
| v2 后测 | `02363932` / runner `845e240e`，60/60 case run、99 测量轮；raw 2 红，逐条复核为 1 个规划失败 + 1 个关键词误报；零动作/车态变化/证据失败/残留挂起，27 键；原丢卡样本 9/9、15 次关闭身份保持、9 份完成引用保留；[首批执行 §5](../design/2026-09-26-v2-runtime-r0-r1-execution.md#5-发布后固定语料复验2026-09-27) |
| 首批客户端与门禁（历史） | 客户端/Go/门禁实际测试 SHA 为 `f4d3b8d7`；HMI 350、Android 112 suites / 1157 tests + typecheck/lint、Go 四包、四门禁/edge smoke 均过；与 release 相同的客户端 tree OID 见 [首批执行 §4](../design/2026-09-26-v2-runtime-r0-r1-execution.md#4-首批合入与部署阻断2026-09-27) |
| 首批容量处置（历史） | 首次 apply 被 30 GiB 门挡住；用户单独授权 51 个精确旧源码/归档路径，清理后 72 份证据元数据 hash 未变、保留全部 release/镜像/存储；原 SHA 重试成功，过程见首批记录 |
| CA2-05 线上契约 | `33c2a731`：17 Agent / 155 在线能力逐项符合冻结摘要；156 项声明中 `mcp-bridge/mcd.order` 未在线暴露；旧/新读取方目录一致；旧版本、非法数值、旧调用方非法参数、流式首事件拒绝及端侧只读查询 5 项通过；`.artifacts/capability-v2/33c2a731-live-contract.json` |
| CA2-05 固定语料后测 | release/runner `33c2a731`，60/60 case run、100 测量轮，raw 1 红：V210 r2 畸形规划 JSON → salvage 闲聊，手册漏答；226 次模型调用均 MiniMax-M3；零动作/27 键车态变化/证据错误/残留挂起，混合手册展示 9/9；[CA2-05 §7](../design/2026-09-27-v2-capability-contract.md#7-真栈契约证据) |
| CA2-05 客户端边界 | HMI / dashboard 的主模块返回 200 JavaScript；浏览器工具连接不可用，未完成实际渲染或设备复验；不转借首批客户端测试数 |
| CA2-05 构建与恢复（历史） | 已授权的 16 项缓存、851 项清单中 271 项、13 个重复源码/上传包路径分别释放约 4.18 / 1.62 / 0.70 GiB；保留发布镜像/数据/证据。构建历史查询触发 daemon panic 后原栈与授权的 7 个 drone-agent 容器已恢复；[事故记录](2026-09-27-buildkit-history-incident.md)。禁止再次调用 buildx history / ListenBuildHistory |
| CA2-06/12 真栈专项 | release/runner `89b19956`，只读 15/15：HTTP/WS 车辆隔离、错车 RPC→WS 拒绝不携带 driving/动作、审计拒绝、26 个有效车态值前后相同；27 个信号元数据中位置 unavailable。signed E2E 连接不等于观测来源已签名 |
| CA2-06/12 固定语料 | release/runner `89b19956`，60/60 case run / 101 测量轮；raw 6 红=5 业务问题+1 关键词误报，228 次已记录调用均 MiniMax-M3；零动作/车态变化/证据失败/残留挂起；混合手册展示 8/9。[逐条复核](../design/2026-09-27-v2-vehicle-state-and-simulation.md#83-固定语料复验) |
| CA2-06/12 客户端 | `89b19956`：HMI 358、dashboard 19 与两者生产构建通过；HMI tsc 与基线同为 25 项旧错，零新增。Android `b98c9b60`：112 suites / 1159 tests + tsc/lint；mobile tree 与 release 相同。浏览器实际渲染与 APK/设备未验，见本包 §8.1 |
| CA2-06/12 签名真栈 | `7b346c90`：26/26 运行 release 镜像一致，23 服务公钥策略、11 个必要消费者一致；私钥只在 edge。真实 NATS 快照独立验签，篡改/重放在隔离校验器拒绝；HTTP/WS 只读 16/16，27 信号/26 有效值、错车无事实并拒绝，前后车态相同；kind 仍 simulated |
| 最近一次固定语料 | release/runner `b095caca`：60/60 case run、99 测量轮（含取消）；原始 2 红=1 关键词误报+1 手册未派发，人工另补场景解释不足与前/后备箱混淆，共 3 个真实业务未完成轮。混合手册呈现 9/9，207 条已记录调用均 pinned MiniMax-M3；零动作/车态差异/证据错误/残留挂起，release 连续、runner 未变。[逐条复核](../design/2026-09-28-v2-permissioned-context-view.md#53-固定语料与逐条复核) |
| CA2-07 权限专项 | `b095caca` 最终探针版本 3：3 组测试历史由 Memory 只读确认存在，12 轮/60 个权限检查通过；6 个受限/关记忆场景无模型调用、无数字回显，零动作/车态变化。正向回答仅 2/3，总业务 passed=false 保留；早期探针中止与旧版越权回显另留工件，不拼接通过数 |
| CA2-07 契约与观测 | `b095caca` 在线 17 Agent/155 能力；旧读取方仅隐藏 4 个迁移后的 Road-safety 能力；9 个契约/拒绝探针通过，legacy 历史清单不改。签名观测 16/16，27 信号/26 有效值、source_kind=simulated，错车拒绝与车态不变 |
| 当前证据索引 | `.artifacts/context-view/b095caca-evidence-manifest.json`；此前来源签名批与来源条件/图标批仍分别使用 `.artifacts/vehicle-state-signing/7b346c90-evidence-manifest.json`、`.artifacts/manual-source-evidence/56409fef-evidence-manifest.json`，不互借数字 |
| 聚合修复容量处置 | 原已授权缓存剩余 580 项仍回收 0B，未扩大范围；另经用户授权清理 6 个已完成发布的重复源码/上传包，约 0.319 GiB，空闲约 30.26 GiB。全部发布/镜像/容器/卷与构建证据校验未变；`.artifacts/vehicle-state-v2/hotfix-space-cleanup-result.json` |
| 来源条件批发布与容量（历史） | `56f92838` 首趟 apply 未创建候选上传/构建目录，原因未定；重新只读预检后按原 API 重试成功。旧获批 580 项本轮仍报告 0B，未追加删除；后续只读空闲约 32.01 GiB，不归为本次清理成果。阶段诊断与边界见 [来源条件 §4](../design/2026-09-28-manual-source-evidence-guard.md#4-首版文字发布与精确证据) |
| 云端容量清理（2026-09-28） | 经用户批准：保留 `56409fef` / `56f92838` / `0a589e14`，删其余 20 个 release 镜像集（含 `4c1f479`，其目录保留）与 19 个 release 目录、重复源码/上传包、09-13 及更早备份 405 个、5 个 08-17/18 迁移包（元数据先归档本地），构建缓存 `until=24h`（同条件补 `--all`）。可用 31.28 → 46.87 GiB；current、运行容器、卷、共享模型与构建证据未变；status 5/5 零 warning，verify `20260928T073945Z-56409fe.json`。第二轮删除首版引导残留（含 1.13 GB 镜像归档）与两份过期 .env 副本等无用内容，可用 48.08 GiB，status 5/5；构建缓存按用户要求保留。过程与保留项见 [history](../agents-history.md)；`.artifacts/cloud-capacity-20260928/` |
| 容量治理 P0 / P1（2026-09-28） | [方案](../design/2026-09-28-cloud-host-capacity-governance.md) P0 `66d68f64`、P1 `48007778` + `887b983c` 已启用：基础设施锚当时为 `a3346202`，2026-09-29 起为 `7c34debf`（`1eb2bcf1`，经新入库的 `dev_stack infra-approval` 批准；其他工作树需基于 main ≥ `1eb2bcf1` 才能部署）；发布 / 回滚后按策略退役旧 release，备份 GFS 轮转（首次手动 apply：备份 119 → 24 套）。随后发布 `55165e50`，发布事务内自动回收首跑：退役 `56f92838`、收尾 `55165e50` / `7b346c90`（删 52 个 tag、3 个目录，当前版本别名因在用跳过），激活前备份轮转 25 套均在策略内；服务器上恰 3 套 release 镜像。status 5/5 零 warning、可用 59.95 GiB，verify `20260928T094959Z-55165e5.json`。P2 `a53034b6` 已安装（18:39 CST）：主机级 `host-capacity-gc` 每小时给构建缓存封顶到 20 GiB（`buildx du` 显示约 21.47GB）、删除 7 天以上的 core dump，journald 上限 1G；不在基础设施锚内，见 `deploy/host/README.md`。首夜 17 轮全部 success、回收 0B（Total 20.6GB，未达上限）；只上传未构建的包（含审批上传目录）自 `1eb2bcf1` 起 72 h 后退役 |
| CA2-08 持久准入 | `753c1a49` 全量 10424 passed / 35 skipped / 11 warnings（637.29 s），四门禁、smoke 13/13；dry-run 零阻断、apply/status/verify 见上两行；在线只读核对 17 Agent / 155 能力、零 durable 声明、契约漂移仍只有 road-safety 4 项，生产 task_ledger 无新列。22 个准入场景在嵌入式 PostgreSQL 16.2 上 22/22，注入缺陷 17 处全部判红。切片 D `fec77afb`：全量 10428 / 35 / 11（522.94 s），在线核对恰 2 项 durable、生产已有新列、无操作头的写请求在准入前被拒；真栈并发双确认两轮消费同一挂起、只下一单 `DC7AA7F65555`、另一轮答「已经处理过」，退款清理完成。两版均未重跑固定语料，不转借上一版读数；[本包 §7](../design/2026-10-01-v2-durable-operation-admission.md#7-发布与线上核对2026-10-02) |
| CA2-09 确认绑定 | `56c5519f` 全量 10446 / 35 / 11（287.20 s），四门禁、smoke 13/13；fakeredis 认领探针 6/6、注入缺陷 13 处全部判红；固定语料 20×3：60/60 完成、101 轮，业务红 4（3 轮未走手册；V207 抢救重试轮原样抄了 `planning.py` 的澄清结构示例「云岚国际中心」，与本包无关），证据错误 0、open operations 0，217 次 LLM 全为 minimax/MiniMax-M3，零动作、零车态变化；取消挂起 3/3 按寻址关闭；p50/p95/p99 7047/17375/28375 ms（p50 升高来自模型侧：规划调用中位 1404→2596 ms）；真栈并发写探针未复跑（需另取授权）。[本包 §5](../design/2026-10-02-v2-confirmation-binding.md#5-实现与证据2026-10-02) |
| CA2-10 结果证据 | `a772e783` 全量 10507 / 35 / 11（302.55 s），四门禁、smoke 13/13；注入缺陷 24 处全部判红；固定语料 20×3：60/60 完成、100 轮，业务红 5（4 轮未走手册、V207 两轮缺「露营」，均为既有签名），证据错误 0、open operations 0，216 次 LLM 全为 minimax/MiniMax-M3，零动作、零车态变化；语料全是只读问句、不含 state_match 步，ResultBundle 未出现 evidence；p50/p95/p99 6860/19781/27782 ms；模拟车上的真栈车控证据探针属于 remote_mutating，未运行（需另取授权）。[本包 §5](../design/2026-10-02-v2-effect-evidence.md#5-实现与证据2026-10-02) |
| CA2-11 车端操作日志 | `b38e5afc` 全量 10584 / 35 / 11，四门禁、smoke 13/13；22 个 CA2-08 准入场景在 SQLite 上全过，内存 / SQLite / 嵌入式 PostgreSQL 三份账本同场景一致；注入缺陷 18 处全部判红；线上卷与库已建立；固定语料 20×3：60/60 完成、99 轮，业务红 3（既有签名），证据错误 0、open operations 0，211 次 LLM 全为 minimax/MiniMax-M3，零动作、零车态变化；其中 15 轮后备箱确认问句经车端准入，线上日志随之出现 15 条记录、0 条 orphaned，云端 step.edge 10–14 ms（含契约探测，一次 292 ms 离群）；p50/p95/p99 7328/18500/28468 ms；授权后的后备箱探针：确认开 / 关各落一条 done、0 orphaned、车态复原。[本包 §4](../design/2026-10-02-v2-vehicle-operation-log.md#4-验证) |
| CA2-15 S1a 端点鉴权 | `e7766e98` 全量 10614 / 35 / 11，四门禁、smoke 13/13；变异 6 条全部判红；status 5/5，verify `20261002T125956Z-e7766e9.json`；真栈只读核对：无 token / 伪造 token → 401、HMI token → 200、冒充他人 `user_id` → 403，线上 HMI 模块带 bearer 且 token 已注入；三个 e2e 本地栈用例未在云端运行，固定语料未重跑（对话链路未改）。[本包 §5](../design/2026-10-02-v2-memory-identity-governance.md#5-s1a端点按-token-解析主体已部署-e7766e98) |
| CA2-15 S1b 删除代际 | `910fd572` 全量 10637 / 35 / 11，四门禁、smoke 13/13；注入缺陷 14 处全部判红；status 5/5，verify `20261002T153258Z-910fd57.json`；固定语料 20×3：60/60、105 轮，证据错误 0、open operations 0，196 次 LLM 全为 minimax/MiniMax-M3，零动作、零车态变化，p50/p95/p99 7375/15781/19438 ms；业务红 9 条全是既有签名类别（「未走手册」5、V207 缺「露营」2、V201 第 3 轮手册没检索到「刚才你说…」2）；只读核对：本次 210 条轮次全部带代际戳，Memory 拒收 0、报错 0；授权后的删除栅栏探针（runner `c624664d`，合成用户 `probe-memfence-*`）PASS：forget 把代际从 0 推到 1；用 forget 前的代际重放轮次、条目、画像三种写入全部被拒（`stale_memory_epoch`）、会话为空；新代际写入照收；清理后会话与代际键都已删除。[本包 §6](../design/2026-10-02-v2-memory-identity-governance.md#6-s1b删除与在途写入同代际失效已部署-910fd572) |
| CA2-15 S2 声音证明 | `fa9cbe9e` 全量 10689 / 35 / 11，四门禁、smoke 13/13；注入缺陷 19 处全部判红；status 5/5，verify `20261002T231742Z-fa9cbe9.json`；线上只读核对：无 token、只自称 `user_id`、伪造 token 三种 S2S `session.start` 都被 1008 关闭（不建会话、不写数据）；Memory / 云端 / llm-gateway / 车端四个容器里的关键文件与提交一致（部署包从 Windows worktree 打出，是 CRLF 形式）；固定语料 20×3：60/60、99 轮，证据错误 0、open operations 0，221 次 LLM 全为 minimax/MiniMax-M3，零动作、零车态变化，p50/p95/p99 6609/21828/31516 ms；业务红 4 条全是既有签名类别（「未走手册」3，其中 V211 因此缺「2.9」；V207 缺「露营」）；手机新包 `xiaozhou-companion-prod-release-fa9cbe9ea-20261003-0726.apk`：构建 exit 0、`BUILD SUCCESSFUL`、包内 `variant=prod build=fa9cbe9ea`；装到测试机 OPPO，设备上 `base.apk` 与本地 SHA-256 一致、非 DEBUGGABLE；免唤醒语音投影与手机 S2S 的真人语音实测未做，小米未装新包。[本包 §7](../design/2026-10-02-v2-memory-identity-governance.md#7-s2声音证明与隐私投影已部署-fa9cbe9e) |
| v2 / Jev | CA2-02–11/12 首版已落地，CA2-15 S1（S1a 端点鉴权、S1b 删除代际）与 S2（声音证明、隐私投影）已部署；CA2-08 首批只覆盖演示商户两项通用写工具。CA2-07 只关闭 Cloud/Agent 模型上下文路径，本包不替 S2S、调试 HTTP、正式全局身份或设备签收。Decide、T1e、OEM、车端日志的后台同步与补偿仍未实现（CA2-11 首版只做按需恢复查询） |

复核当前现场先运行 `python scripts/dev_stack.py target show`，再按授权范围运行 status/verify 与专项探针。
`origin/main`、生产 release 和设备包不是一个版本号；5/5 健康也不能证明完整业务正确。

本页所有 `.artifacts/` 路径都是根仓本地 ignored 证据，不随 git clone 移植；
缺失时应按原记录的精确 release/模型重新取证，不能把文档记过当作 artifact 仍在。

## 3. 已闭合的 QA 范围

### 3.1 探索式真实用户 QA

- 2026-08-15 的 533 个真实业务轮、58 个问题已经完成去重、根因分类和立卡；
- Q1–Q13、Q5 残余、person-pickup 和编号序列最后一条 I-024 均已完成；
- 根因卡与重判保留在
  [`2026-08-15-qa-exploratory-root-cause-cards.md`](../design/2026-08-15-qa-exploratory-root-cause-cards.md)；
- 原始双模型报告保留在
  [`2026-08-15-exploratory-real-user-qa-deepseek-minimax.md`](2026-08-15-exploratory-real-user-qa-deepseek-minimax.md)。

### 3.2 MiniMax 云端修复批

- 2026-08-26 原始长会话自动读数为 282 PASS / 33 FAIL，但自动 PASS 不等于业务正确；
- C1–C16 六批、M1–M6 余项和 B1–B7 全量收尾已经落地；
- 完整根因、批次设计和真栈读数查
  [`2026-08-27-minimax-qa-root-cause-fix-plan.md`](../design/2026-08-27-minimax-qa-root-cause-fix-plan.md)；
- 原始问题/trace 查
  [`2026-08-26-minimax-cloud-qa-findings.md`](2026-08-26-minimax-cloud-qa-findings.md)。

### 3.3 安全确认写闸

以下执行出口已经共用同一份问句副作用判据：

1. Planner focused/normal build；
2. adaptive replan receiver；
3. Agent `_escalate` mini-plan；
4. fallback capability scan；
5. D0/T2 流式 action 出口；
6. 挂起恢复后的再规划与改派。

`Capability.response_only` 由 manifest 声明，经 Registry round-trip、`Step` 装配和恢复态保持；
Executor 在 dispatch 前拒绝 `response_only + require_confirm`，在 dispatch 后拒绝 action、
`NEED_CONFIRM` 和 `NEED_SLOT`。`safety_origin_text` 由服务端盖章并跨 replan/suspend/restore
保持，LLM `goal/reason` 与当前补槽短句都没有安全授权权威。

专题设计与实现证据查
[`2026-08-30-qa-safety-confirmed-write-guard.md`](../design/2026-08-30-qa-safety-confirmed-write-guard.md)。

### 3.4 Planner null 槽

长会话中 MiniMax 曾输出 `limit:null`；旧装配将它写成字符串 `"None"`，Info Agent
执行 `int("None")` 后返回“Agent 内部错误：ValueError”。release `a729b98` 的修法是在
`_validated_steps` 中把 JSON null 当成“未提供”，同时保留 0、false 和空字符串。

长会话探针也已补上通用守卫：任何以“Agent 内部错误”开头的话术必须判红，不能再藏在
自动 PASS 中。

## 4. 生产复验

### 4.1 安全专项（release `e9fa602`）

`probe_qa_regression.py --group safety --repeat 3`：5 例、15/15 PASS。

- 红色机油灯首轮、跨轮追问和“慢一点开”均给出停车/熄火建议；
- 疲劳驾驶后拒绝提醒仍不允许继续危险驾驶；
- 零动作、零挂起、未进入商户写能力。

Artifact：`.artifacts/dev-stack-verifications/qa-safety-e9fa602-repeat3.json`。

### 4.2 完整 information persona（release `e9fa602`）

官方长会话结果：57/59 PASS、1 warning、零中止、零 cleanup failure、fallback=0、
104 次 LLM 全 pinned。

清理结果：

- 测试提醒完成创建 → 改期 → 取消，最终列表为 0；
- 活动导航最终发出 `navigate_cancel`；
- 零 open operation；
- 全程无商户 intent/预览卡，因此无商户草稿；
- release 首尾均为 `e9fa602`。

Artifact：`.artifacts/dev-stack-verifications/qa-long-information-e9fa602.json`。

### 4.3 新闻专项（release `a729b98`）

3 个干净会话、每个 5 个新闻业务轮：15/15 无 internal error，零中止、零 cleanup failure、
release 连续。Artifact：`.artifacts/dev-stack-verifications/qa-news-repeat3-a729b98.json`。

### 4.4 真实车型手册（历史生产 `434a046`，36 题已闭合）

- 278 页 `SU7用户手册` 生成 269 个文本 chunk、350 个图片放置 / 299 个 blob；v2 包
  `648cdf3d…400ed` 经 shared-model bootstrap 只读挂载，启动决议仍为 approved real provider；
- release `434a046` 的完整 36 题精确落域 36/36、内容 36/36；14 个高风险问法全部 3/3，
  合计 64/64；
- 64 轮均为单一 `manual` 卡、approved real provenance、零 action/need_confirm/probe error；逐轮
  读取完整车态，最终 diff={}；雨刮与安全带俗称继续返回正确手册图片；
- 统一 verify、5/5 endpoint healthy、零 warning；verify artifact
  `.artifacts/dev-stack-verifications/20260903T130534Z-434a046.json`。手册真栈 artifact
  `.artifacts/manual-rag-live-validation/20260903T130655Z-final-434a046.json`，SHA=`3fed8c94…d63a`；
- exact 代码本地全量 7833/34/4，确定性 retrieval 36/36。这里关闭的是原36题基准项，不代表
  整本手册范围全绿；2026-09-04 扩面结果见下节。

### 4.5 整本手册范围复核（历史生产 `7b594f37`）

- 原 PDF 重建与 `.mrag` 逐字一致；离线候选的269页、160索引路径、187 outline叶子、35视觉
  语义、原36题均全绿，证明当前失败不支持“应改向量库”的结论；
- 当前生产自然化187叶子首轮181/187（96.79%）；6条失败后两轮均6/6，全部为2/3，
  无0/3稳定章节失败，但不能写187/187稳定通过；其中哨兵模式首轮有1次opening-handshake timeout；
- 当前生产视觉30/35（85.71%）；位置灯、左右转向、后雾灯、近光灯为0/3稳定零命中；
- 章节主批+失败复验199轮、视觉主批+失败复验45轮均action=0、need_confirm=0、完整26项
  车态diff={}；章节主批有上述1次transport probe error，视觉为零。初版不安全名词短语探针曾发
  一次幂等`hvac.on`，已中止并由双安全预检取代，不能藏掉；
- 原36题当前整批有7条旧表述在联网前被安全预检拒绝，故不报当前36/36；用户点名的雨刮与
  “小人背宝剑”安全问法已各3/3，均返回预期PDF页和图片，零动作、车态不变；
- 本地候选只扩三字caption的视觉语境匹配，并在manifest增加明确SU7手册来源的窄hint；离线
  视觉35/35、显式来源222/222；该候选随后发布并继续修复LLM故障降级，终态见下节。证据与hash见
  `docs/design/2026-09-04-xiaomi-su7-manual-rag-full-coverage-validation-plan.md` §5。

### 4.6 整本手册生产闭合（历史验收锚 `9a3b6f2f`）

- 0.3.2首次发布到`805711cf`后，五个三字caption稳定缺口消失；但章节主批184/187、视觉
  34/35仍出现4次`Agent 内部错误：RuntimeError`。Trace证明均已落`manual.query`且Agent执行
  失败，不是路由或BM25问题；
- 0.3.3对LLM RuntimeError做一次有界重试，配额/参数/鉴权类不重试；仍失败时返回已检索的真实
  PDF卡与诚实降级话术，ValueError等编程异常继续显式失败。精确代码全量 7861 passed / 34 skipped / 5 warnings，0 failed；
- 发布后独立章节批 **187/187**，p50=9365.659ms、p95=14446.685ms、max=34710.202ms；视觉
  **35/35**，p50=7886.125ms、p95=13456.285ms、max=21204.482ms；
- `雨刮器怎么打开`与“小人背宝剑”各3/3，分别稳定返回PDF第95/193页与对应图片；所有正式轮
  action=0、need_confirm=0、probe error=0、完整26项车态diff={}；
- 统一verify、5/5 status均通过。章节主artifact SHA=`508756d6…baf80`，视觉SHA=
  `7c05d04a…e0762`，全量日志SHA=`ab40f81e…d91df`，verify SHA=`7a521902…ad44`。

## 5. 当前活项

2026-09-28 接续：手册口语/复合句尚有规划方差；CA2-04 已承接确认轮完整结果，但不能替代上游派发/成功完成。
本次新增空检索、后续问句落域和已派发超时按 [手册 §8 后续记录](../design/2026-09-26-manual-rag-colloquial-recall.md)维护；
聚合数值角色改写已修；V211 上游条件遗漏已由 [来源条件护栏](../design/2026-09-28-manual-source-evidence-guard.md#5-受控图标补口)
在已派发的登记车型/主题内消除遗漏；图标补口通过。仍有 MSE01 一次重试丢步骤的真实漏答，见同记录 §5，
不因来源护栏成功销账。车辆来源签名已启用并完成本包验证，不能替代这些业务修复。
取消后的答案回忆还存在三次错误否认已回答内容；完整结果引用本身仍在，根因待定位，见同修复记录 §5.1。
签名发布的固定语料又观察到手册 15 s 超时、目录模型超时后空检索、无必要澄清、畸形 steps、
Provider 529 后的受话误判，以及露营模式解释覆盖不足；自动判据对“重复关键词但没有回答”存在盲区。
六个原始红轮与四个补充未完成轮均只记录未关闭，见 [本批逐条记录](../design/2026-09-27-v2-vehicle-state-and-simulation.md#84-签名车道启用与真栈复验2026-09-28)。
CA2-07 另登记：V210 r1 手册未派发，V207 r3 场景列表未回答模式含义，V214 r3 生成层混淆前/后备箱；
权限专项的正向回忆也有一轮声称无记录，但数据存在，Agent 具体输入缺乏完整证据，根因待裁定。
这些都没有随权限机制签收而关闭，见 [CA2-07 §5](../design/2026-09-28-v2-permissioned-context-view.md#5-发布与证据2026-09-28)。
2026-10-02 CA2-10 真栈车控探针（两次授权）新增三项：① 车端把「如果深圳今天不下雪，就把空调打开」拆开，后件当场执行、空调被无条件打开（「温度低于20度时打开空调」也会被当成设 20 度）——**已修**，`9d6b0509` 整句上云，判据与规划器共用；② 修后云端 T2 查完条件不派后件（goal「条件式指示不产生本轮动作步」，trace `ac80349a236c4747b074dc1cd8c8b162`），未修，改规划知识需 A/B；③ 规划为空步时谈话步说「好，关空调这步执行完了」（trace `70b966cf482846969e001e1b320d93d2`），执行声明闸刻意只认「为您」类标记，放宽前要先拿历史谈话量误伤，未修。另有「下雨的话…」「等到了公司再…」两种延后形态端云两侧都不认，未修。见 [CA2-10 §5](../design/2026-10-02-v2-effect-evidence.md#5-实现与证据2026-10-02)。
会话四轮未关项按 [原待办](../design/2026-09-24-conversation-review-round4-remediation.md) §7 重证；
Android 按 [总表](../design/2026-09-14-android-remaining-todos.md)。统一优先级见 [路线图](../roadmap.md)，
下面保留历史 QA 五项的逐条处置，不代表全部仍待修。

2026-09-19 逐条收口，过程与证据见 [QA 轮剩余活项收口](../design/2026-09-19-qa-residual-closeout.md)。

- **2026-09-11 那条「端侧新闻规则误判 `media.play` / Planner 漏拒」已于 2026-09-14 修掉并发布**
  （`696899b5`，随 `9ced633b` 上生产）：端侧 `runtime/reported_speech.py::is_reported_speech` 语域闸 +
  Planner「语音来源 ∧ 播报语域 ∧ 两轮空手 ⇒ 不受话」；发布后只读复跑 24 轮，播报语域 11/12 静默拒识、
  首轮失败原话端侧不再执行、零动作。**没修的那一半**：乘客句「他昨天跟我说那个项目黄了」没有播报语域，
  文本上与「用户向助手转述」不可区分，归声学（真人 + 背景源），见
  [逐条核实](2026-09-11-voice-input-acceptance-live-findings.md) 末段。

| 活项 | 已登记处置状态（原 09-19 表，含后续补记） | 证据 / 下一步 |
|---|---|---|
| 安全问句偶尔落 `info.search`（T24） | **已修、已发布 `1eb25a70`、真栈复验通过** | 根因是 manifest 没把 Agent 早就实现的「告警 ⇒ 按等级给续驾结论」说出来（planner 只看 description），既有 hint 只认「高速/路上」开头。修法全在 `agents/road_safety/manifest.yaml`：描述 + 续驾 hint（122 < manual 124，手册地盘不动）+ 话术「出现X时」；`test_route_hints.py` 37 passed、`eval_route_hints` 118/118、范例 +1、四门禁全过、两处变异判红。真栈（`1eb25a70`，`minimax:MiniMax-M3`）：干净会话「红色机油灯亮了还能继续开吗」**3/3** `safety.driving_advice`、`safety_advice` 卡 `_prov.mode=deterministic vendor=road-safety`、零动作零挂起（trace `fe64eab2c9da4a17972c02b4f2f04df9` / `206c1e0acad748e8968adb821a577c6e` / `1090381819284b6e8be35f2953884416`）；「水温报警了还可以继续行驶吗」1/1（话术「出现水温报警时不要大意…」）；对照「机油灯亮了怎么办」2/2 仍 `manual.query`；`probe_qa_regression --group safety --repeat 3` **15/15**（SF3 三趟 manual → safety → safety 零动作）。information persona 整场未重跑，T24 那一格只在这里闭合 |
| safety focus 持续阻断后续 charging plan（T47） | **已裁决（A）、已发布 `0d414816`、真栈 T47 格 3/3 闭合** | 用户 2026-09-19 裁 A；同日实施：`runtime.safety_signal.alert_resolved`（完成态解除陈述 ∧ 点名告警对象 ∧ 非问句非指令非否定；「我会靠边」不算）⇒ 编排清焦点 + `safety_alert_cleared` 旗挡接力；road-safety / chitchat 解除轮不再读旧告警；`alert_level` 去掉解除分句（「机油灯灭了但是水温灯亮了」仍登记水温灯）。runtime 13 / focus 25 / 三 Agent 139 / cloud+runtime+edge 2853，四处变异判红；persona 补解除轮。真栈（`0d414816`，persona 同形序列 ×3）：解除轮 3/3 chitchat 零动作、**T47 句 3/3 `charging.plan`**、后续「现在还能继续开吗」3/3 无「未解除」（收口页 §3.4）。顺带抓到并修掉两处「解除/告警陈述到不了输入侧登记」的漏点：① `dest_choice` 挂起把它整句当目的地吞掉 ⇒ `_is_topic_change` 安全信号一律判换题（**已发布 `b342e3bb`**，带挂起序列 ×3 零吞句）；② 规划轮在技术失败 / 授权缺失 / 澄清 / 取消未命中 / 没听清五条出口提前 `return`，`extract_focus` 没跑到 ⇒ `_register_input_facts`（**已发布 `ca4bf370`**，真栈：解除句落技术失败出口那一趟 T6 不再「未解除」，收口页 §3.5） |
| MiniMax TTS 长文本 / RPM 边界 | **按 2026-09-06 证据销账** | 原 887 字样本的服务商回包 `rate limit exceeded (RPM)` 是 08-30 的历史证据；生产 `a09c73a`：931 字整段 2 请求（修前 79 请求、4×60s 等待、4 个 ~20s 空白）、204.6s 音频完整、`sim_underruns=0`；账号级共享限流桶（并发配额）；泓舟人耳 OPPO / Xiaomi 两机 ✅（Xiaomi 176.2s 音频 underruns 0、gaps []）。**仍开、独立记**：混合意图轮盲听；长会话探针的 TTS 采样车道自 `e9fa602` 后未在新 release 重跑 |
| barge-in 在途残帧 | **裁决完、判据已改** | 客户端必须丢弃且已在丢弃（HMI / mobile `disposed` 守卫；CDP C14；mobile 新用例钉住 6144 / 8192 字节不进播放器）；服务端「零字节」在全双工上不可判，改为「最后一片残帧 ≤ 1s 在途窗口 ∧ ≤5s 关闭」（`probe_qa_long_sessions.py::_BARGE_IN_FLIGHT_MS`），未计时的残帧仍判红。下次长会话跑批生效 |
| 全量 warning | **gRPC fixture 债务已修；其余分类留档** | gRPC `UnaryUnaryCall._invoke was never awaited` = trip 测试三次 `asyncio.run` 共用真 `LLMClient`（经系统代理各等一轮超时），改显式「不可达」替身，trip_planner 99 passed 零告警、9.86s → 0.73s。Starlette `httpx2` 弃用（第三方，换依赖是红线）、`audioop` 历史项已在 09-25 的 `0a876b73` 移除（见会话四轮 §5.7）、regex / WordPiece（第三方）留着不藏。当前全量条目见收口页 §7 |

2026-09-08 Android AR04 的发现与处置（客户端包、源码修复与生产分别记录）：

- 后端 `a09c73a5da3181708279bc1f3e90acb1519606a0` 的提醒错域仍未修。原 trace `21798d30258aa5bf` 已查明实际 `minimax:MiniMax-M3` 首次生成非法 steps，重试给空计划，随后 `toolcall_degraded → chitchat.talk → info.search`；数据库零创建。问题在格式化规划失败及后续降级，不能从这一个样本推导稳定错域率。
- 提醒标题污染已由 `573ad46` 修复并于 2026-09-09 发布；220 条服务测试与 CI 通过，真实创建 trace `510339013295c9a7` 已证数据库 title 不再带创建指令前缀。成功创建 trace `458440c431c99c8f` 的 Planner 原 title 已是干净标记，污染发生在 Agent 回填原话；五条旧记录没有被改库清洗。
- 对话页“说话可打断”提示已由 `a731973` 修复为“播报中”；OPPO 当前包 `573ad46d9` 已在实际播放时取证，麦克风计数为 0，不再承诺直接语音打断。此项客户端缺陷已修，不能据此关闭更广的音频矩阵。

OPPO 默认/全屏折叠与本地征询/草稿保留证据已取；2026-09-09 新增一条真实 Keyguard 提醒也已完成：锁屏/亮屏未解锁均不提前 ACK，解锁呈现后只播一次，本地收起后重入无重播。临时偏好均恢复。当前证据见 [AR04 第十四节](../design/2026-09-08-ar04-presentation-ack-implementation.md)；原五条回执保持 `065efd85e` 锚，新提醒/发布绑定 `573ad46`。服务端多 operationId 实机组合等仍缺，QA 非全绿。

以上活项是独立问题，不反推安全确认写闸未上线；同样也不能因为安全闸已上线就把它们写成已关闭。

## 6. 证据纪律

1. release、全量测试和真栈 artifact 必须绑定同一个明确 SHA；
2. 邻近 SHA 的单测不能转借给已部署 SHA；纯 docs/test 提交要明确写“HEAD 领先 release”；
3. 探针自动 PASS 不是业务正确，必须读 `fails`、trace、actions、card 和 cleanup；
4. 单次采样不能当基线；有模型方差的轮至少 `--repeat 3`；
5. 回放只证明“当前尺子会如何重判旧话术”，不证明当前系统会生成同样话术；
6. 长会话必须显式 `--expected-sha`，默认 HEAD 在 docs/test 领先 release 时会正确拒绝；
7. 商户写、支付、真实车控、数据删除和系统配置仍要单轮人工授权。

## 7. 接手路径

只处理当前 QA 活项时，按以下顺序：

1. 本页 §2、§5；
2. 安全专题设计 §12：
   [`2026-08-30-qa-safety-confirmed-write-guard.md`](../design/2026-08-30-qa-safety-confirmed-write-guard.md)；
3. MiniMax fix plan 的最终状态与部署后读数；
4. 对应 artifact 和 collector trace；
5. 需要历史原因时再查 `docs/agents-history.md` §84–§88。

不要从已完成的 superpowers implementation plan 继续顺序执行；它们是实施记录，不是当前待办。
