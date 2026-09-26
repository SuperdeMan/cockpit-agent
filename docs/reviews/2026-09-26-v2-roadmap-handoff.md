# v2 路线图与 Jev 研究采纳交接

> 日期：2026-09-26。状态：文档整合；新增运行时实现未开始。
> 源码/文档审阅基线：`47c62b44d335a3c76da90f89f05fa2fc887c2742`。
> 用户授权：详细阅读项目和两份研究，更新路线图/架构/入口并拆解实施，允许所需提交与推送。

## 1. 交付入口

| 读者要做什么 | 唯一维护位置 |
|---|---|
| 决定下一步和阶段门 | [路线图](../roadmap.md)：R0–R5、Jev 支线、资源假设、旧待办映射 |
| 理解当前架构与目标差异 | [架构主文](../architecture/cockpit-agent-architecture.md) + [v2 目标分册](../architecture/cockpit-agent-v2-target-architecture.md) |
| 领取可独立审查的任务 | [实施方案](../design/2026-09-26-cockpit-agent-v2-implementation-plan.md)：CA2-01–22 / JV00–09，首轮 PR 与验收 |
| 查看最后记录的 release/测试/QA | [QA/发布交接 §2](2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界) |
| 接手工程规则 | [AGENTS.md](../../AGENTS.md) + [CLAUDE.md](../../CLAUDE.md)工程细则 |
| 回溯研究证据与未验证假设 | [Jev 研究](../research/2026-09-25-cockpit-agent-jev-integration-plan.md)、[v2 RFC](../research/2026-09-26-cockpit-agent-v2-upgrade-rfc.md) |

## 2. 关键判断

- 可靠性主线先补步骤归属、稳定任务身份和完整结果，再补可信车辆状态、持久操作与确认/对账。
  Task Ledger、Outcome Verifier、Step.kind、MCP 桥已经存在，方案按原位扩展编排。
- `PLANNER_GOALS` 有负向 A/B 记录且默认关闭；稳定 goal_id 不依赖重新开启模型 goals/covers。
- Jev 仅为独立 Decide 建议通道，先 JV00–03 的契约/影子/中文校准；收益不成立可停，不阻塞 v2。
- Jev 研究基线比当前代码旧：JV05 已加上 09-26 的目录路由、词法首页保留和复合问句主语承接，
  先证明文本/页码/图片等价，再单独测重排；确认轮丢完整结果归 CA2-04。
- 多车身份/逐信号时效涉及所有事件生产者和消费者；旧单车镜像不能局部改一处就宣称完成。
- R3 端侧实验与 R4 车辆服务解耦；24 周是 3–5 人稳定投入及外部条件就绪的估算，R5 只到集成试点。

## 3. 文档迁移与保留

AGENTS.md §4.0–§4.2 的发布流水和旧待办表完整保存在
[入口状态快照](../history/2026-09-26-entry-status-snapshot.md)，原来规则/验证章节号继续保留。
当前发布记录从入口表集中到 QA 交接，避免当前/上一版几十条链反复拷贝。

架构 §5.2.3–§5.2.17 的详细机制与证据分拆到
[Planner 约束分册](../architecture/detailed/planner-invariants.md)；主文保留原标题和编号作跳转，
不删除既有安全设计。完整版本记录另存[架构版本历史](../architecture/detailed/architecture-version-history.md)。
历史 Phase 1 WS 与原研究均保留，增加接续关系，不把旧验收结论抬升成 v2 已完成。

README 及 Cloud、Edge、LLM Gateway、SDK、Memory、manual-rag、Skills、HMI、Android 的说明
分别指向自己负责的工作包；conventions/dev-guide/test 只登记规划边界与验证入口，
未向现行协议表冒填未实现字段/环境变量。

## 4. 证据与范围

逐项核对了 proto LLM 接口、Step/step_record、PLANNER_GOALS、挂起摘要、WorkingSet、
Cloud 状态镜像、Verifier、SDK Ledger、端侧 NLU、手册 retrieve/materialize/目录合并接点，
并以服务 README、QA/会话修复记录和 Android 总表交叉确认当前边界。
官方 Jev、AOSP VSIDL、A2A/MCP 与两篇核心方法论文的复核入口保存在实施方案 §9。

初始工作树只有用户提供的两份研究未跟踪；fetch 后 HEAD 与 origin/main 均为 `47c62b44…`。
读取 `dev-stack.local` 并运行 target show，目标为 cloud；本轮没有运行 Compose、线上探针、Jev 付费调用或部署。
生产最后登记 `5a2f4c9d…` 的证据沿原记录保留，不宣称本轮重新验证了现场或全量业务。
变更范围为 Markdown；没有改运行配置、CI/CD、schema 或运行时代码。

## 5. 本轮验证

已完成：

- 既有文档/发布手册守卫：`python -X utf8 -m pytest -q scripts/tests/test_cloud_deploy_assets.py -k 'docs or runbook'`，**5 passed / 193 deselected**。
- 本轮 30 份 Markdown 的 480 个本地文件链接、24 个带 fragment 的锚点检查：失效 0。
- 原 AGENTS 状态段与 Planner 约束段在统一相对链接基址后逐字比对通过；旧架构版本历史完整保留，另增 v1.52 记录。
- CA2-01–22 与 JV00–09 编号覆盖完整；历史 Phase 阶段的入站锚点没有待修链接。
- `git diff --check` 通过；改动只有 Markdown，AGENTS 从 255 行 / 141133 字节收敛为 239 行 / 15322 字节。

没有新建或改写测试来迁就文档内容，也没有运行后端全量、真栈或新模型性能验收。
文档检查不能充当 v2 实现验收、后端全量通过或生产已更新。

## 6. 下一位执行者

先读取路线图与实施方案，运行 target show / git status 核对实际版本，领取 PR-A（CA2-01/JV00）。
后续 PR-B/C/D 依次做步骤归属、稳定身份、ResultBundle；Jev 网关/快照可以在接口冻结后独立做。
实际人力、目标板/OEM、中文标注与真 API 条件缺失只阻塞对应包，不能靠文档把这些条件视为已具备。

本轮提交/推送授权已明确；后续部署、真实副作用、数据库迁移、密钥/运行配置及 CI/CD 变更
仍按项目规则交付具体可审查结果后分别处理。
