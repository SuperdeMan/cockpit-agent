# EVA 二轮对标实施计划（批 A-E）

- **状态**：已批准（泓舟 2026-08-14 确认 [`缺口分析`](2026-08-14-eva-round2-capability-gaps.md) 的修复方向与优先级，并要求把 §5 存量问题一并纳入；commit/push 已授权）
- **范围**：P0=G1/G6、P1=G2/G7/G3、P2=G5/G11/G9(导航半边)、存量问题①-⑦。**不含** P3（G4 主题行程 / G8 导航会话状态 / G9 trip 跨城市——维持「P0/P1 落完后另立 RFC」）与 G10（生态搁置）。
- **交付对象**：本会话实施；实施记录逐批写回本文档 §7

## 1. 批次划分

| 批 | 内容 | 主要落点 |
|---|---|---|
| A | 存量问题①-⑦ | nearby provider 排序、trip 序数、governor owner 分组、死槽位摘除、conventions/README 记账 |
| B | 导航域五刀：G1 时间约束 + G2 沿途取点 + G3 模糊推断扩域 + G11 路线策略 + G9 多途经点 | `agents/navigation/` + `agents/_sdk/landmark.py` + exemplars |
| C | G6 记忆消费面四环 | `proto/cockpit/memory/` + `memory/` + `agents/nearby/` + `agents/navigation/` + `orchestrator/cloud/context.py` |
| D | G7 事件记忆→询问式主动 | `memory/extract.py` + `memory/server.py` |
| E | G5 nearby 类目/属性 | `agents/nearby/` |

## 2. 关键设计决策（与缺口分析 §2 的差量）

1. **G7 形态定为询问式两段**：抽取识别到未来事件（LLM 输出 ISO `event_time`，确定性校验=可解析、在未来、90 天内）→ 巩固期经既有 `_emit_proactive` 发 advisory 建议卡「我记下了：{事件}。要到时候提前提醒你吗？」，按钮 `send_text` 回发「{时间}提醒我{事件}」走正常语音链建 reminder。**不自动建 reminder**——主动路径零执行权不为此开口子；用户未答即无契约。
2. **死槽位一律摘除不接上**（B4 纪律：无消费方的声明只会漂移）：nearby `radius`/`price_level`（与 price_max 重复、provider 无参）、`nearby.order` 的 `datetime`/`party_size`（订座属 G10 搁置面，桩本就不读）、charging `departure_time`（语义属长途出发规划，与本批 arrive_by 不同题，待真需求再声明）。
3. **G1 范围收敛到 navigation**：`arrive_by` 槽只上 `navigation.navigate_to`（charging 不动）；解析用 navigation 内小型确定性解析器（HH:MM / N点[半] / 上午下午晚 段位 / 「N点前」，双候选取未来最近——不搬 reminder 的 timeparse，跨 Agent 抽公共件等第二个消费方出现再说）；出发提醒=REMINDABLE 反向环（`fire_at = arrive_by − duration − 10min`，reminder 侧词形按「出发/到达」择项）。候选 ETA 标注只算前 2 个（latency 预算内 best-effort）。
4. **G2 沿途取点不搬 weave 原函数**（它绑 SoC 语义）：navigation 侧新增按路线 polyline 里程 40%-60% 段取样中点 → 以该点为圆心搜 stop_category；拿不到 polyline 回落现状（目的地附近）并在话术如实说「目的地附近」。
5. **G3 只扩入口与 prompt、不放开无门 LLM**：marker 增俗称族（秋裤/裤衩/小蛮腰/开瓶器/大铜钱/铁裤衩 等封闭词表）与「自然地物名词×形容词共现」（湖/山/岛/塔/桥/楼 × 巨大/特别/最大/最高/圆/像）；prompt 补自然地物域与**城市约束优先于内建示例**；保留地图校验与 dest_choice 确认。
6. **G6 proto 演进向后兼容**：`MemoryItem` 加 `subject`（关于谁，亲属称谓经 relation 同义词表归一，缺省=自己）与 `polarity`（like/dislike/空）；`RecallRequest` 加 `subject` 过滤。四个消费环各配「消费方证据」测试：①口味→检索前置（修假个性化：偏好在 search **前**生效，话术只报真实生效项）②负偏好→结果软降权（含「这家太酸」按店名/品类降权）③导航成功落 episodic 轨迹 + 「上次/上回/那次」触发 episodic 召回④route 偏好→G11 strategy 消费。
7. **G11 策略映射**：`route_pref` 槽（避堵/不走高速/高速优先/少收费）→ 高德 driving strategy 参数；槽缺省时查记忆 route 偏好（G6 ④环）；「风景好的路」降档为「大路优先+不走高速」并诚实话术。
8. **G9 导航半边**：`waypoint` 槽兼容「A、B」多值与「途经A和B」原话，逐点解析保用户序进 `payload.waypoints`（高德 16 点上限内），route_plan 卡照常；不做地理重排（那是 P3 的题）。

## 3. 验收

- 逐批：所辖模块测试全绿（nearby / navigation / trip_planner / proactive / memory / orchestrator/cloud / reminder）+ 涉及 exemplars 时 `test/eval_exemplars.py` 门禁绿。
- 收尾：干净 env 全量 `python -m pytest --import-mode=importlib` 与 §4.0 基线对账（预期净增）；`scripts/check_intent_gate.py` strict、能力完整性门禁、HMI `node --test`（若卡片字段有增量）。
- 每个 G6 消费环必须有一条「记忆改变了行为」的断言（不是「记忆被注入了」）。

## 4. 明确不做（本批）

G4 / G8 / G9-trip 跨城市（P3，另立 RFC）；G10 订座/票务（搁置）；真栈全量 journeys 重跑（本批后单独安排，先以单测+契约+门禁收口——涉及 proto/容器的改动在收尾批注明未经真栈项）。

## 7. 实施记录

（逐批回填）
