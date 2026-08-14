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

## 7. 实施记录（2026-08-14，五批全部合入 main）

| 批 | commit | 落点摘要 | 验证 |
|---|---|---|---|
| A | `51c4992` | 七条存量问题：nearby 评分先排后截（回归测试钉死 limit 外高分店进前排）/ trip「第一站」全程序数（两测试先红后绿实证 bug）/ 治理器 `_flush` 按 owner 分组（跨乘员断言）/ 死槽位五处摘除 / conventions §9.18 + 两 README | nearby 58 / trip 47 / proactive 62 |
| B | `04ff730` | 导航五刀：G1 `arrive_by`（slot+原话兜底、裸时刻未来最近消歧、ETA 三档量化话术、绕行Δ、候选 ETA、REMINDABLE 出发反向环+reminder 词形择项）；G2 沿途 45% 采样点（回落如实说）；G3 landmark 俗称词表+自然地物共现+prompt 城市约束最高优先；G11 route_pref→高德 strategy（风景诚实降档）；G9 多途经点保序 | navigation 92 / reminder 157 / trip+charging+sdk 172 / 范例门禁 / L0 2/2 |
| C | `ad0f2e8` | G6：proto `subject`/`polarity` + `RecallRequest.subject`（缺省空=存量逐字不变）；冲突域按 subject 收窄；抽取输出 subject/polarity/event_time + route.* 谓词归一；**四个消费出口**——nearby 口味检索前置（修假个性化）+负偏好软降权+subject 感知、navigation route.* 记忆→strategy、导航落 episodic 轨迹、planner 历史指代放开 episodic 召回；catalog 锚点 11866→11928 留痕 | memory 213 / nearby 61 / navigation 96 / cloud 全套 |
| D | `32fcdaf` | G7 询问式：抽取当轮的未来事件（event_time 确定性校验）→ `reminder_card(context=offer)` 建议卡，「要的」send_text 回发正常链建 reminder（**零执行权不破**、标题剥双时间词、new_ids 门控只建议一次、过去事件永不打扰） | memory 215 / HMI node 258 + Vite build |
| E | `7d47959` | G5：类目表补动物园族；「餐饮」默认只在饮食信号下成立、否则诚实追问；氛围词软重排+「地图没有安静度数据」诚实话术 | nearby 64 / 范例门禁 291 / L0 2/2 |

**实施期修正记账**（方案 vs 实测）：
1. 批 B 自埋缺陷被自己的测试抓住：`N点` 数字时刻用单字符类会把「23点」错拆成「3点」——改 `\d{1,2}` 交替分支后回归钉死。
2. 批 E 首版饮食信号词表过窄，「川菜馆/火锅」够不着被两条既有测试打红——信号面扩到 菜/火锅/烧烤 并计入 cuisine 槽。**「诚实追问」的代价是要把「本来就是餐饮」的判定面配齐**，否则诚实降级会误伤主流路径。
3. 途经点迟到话术首版引导「说『途经点不去了』」——那是 G8（未实施）的能力，**话术只许诺今天走得通的路**，改为「直接导航去X」。
4. catalog 预算锚点在批 A/B 后变红（+62），按该测试自身纪律更新并留痕——锚点测试守的是「完整 inventory 不超预算」，不是冻结条数。

**遗留（本批范围外，维持缺口分析的 P3/搁置判定）**：G4 主题行程、G8 导航会话状态、
G9 trip 跨城市（各需独立 RFC）；G10 订座/票务（搁置）。真栈 journeys 全量重跑与容器
rebuild 未在本批执行（改动经单测/契约/门禁收口；memory proto 变更后首次 `make up`
需 `--build` 重建 memory/cloud-planner/nearby/navigation/reminder 及依赖 `_sdk` 的服务）。

## 8. 真栈端到端验证（2026-08-14 当日，`make up --build` 30 容器 + EVA 语料探针）

13 轮语料（8 导航即时 + 5 记忆链）经 edge-gateway WS 真栈跑通，探针
`scratchpad/eva_probe.py` 形态（位置=上海人民广场，session `demo-eva-*` 让记忆抽取照常）。

**首轮 8 通 5 有发现 → 三条修复（批 F，当日验证合入）→ 复验全通**：

| 修 | 真因（trace 实锤） | 修法 |
|---|---|---|
| F1 A1 被 demo 下单劫持 | `s_hint_shop_order`：mcp-bridge 的 demo 咖啡 **replace hint** 命中「买杯咖啡」，guard 让路词表没有导航语境——guide/范例都注入了也拦不住 LLM **之后**的整条改写（trace 21f99cb3；「hint 写错是事故」活标本） | guard 增 `导航\|路上\|顺路\|沿途\|途经\|带我去…`（只增不减）；route hints 评测 80/80 无回归 |
| F2 B2b 记忆路线偏好不生效 | 抽取把「不要走高速」合理标成 `polarity=dislike`，而消费环首版按 dislike 排除——**route.\* 的方向已编码在谓词名里**，按极性过滤是自己跟自己打架 | `_route_pref_from_memory` 撤销极性过滤，回归测试改用 dislike 形态钉死 |
| F3 「上次那个湖」召不回 | 轨迹写入只挂了 `_route_plan_to`，而「圆圆的湖→滴水湖」走 `search_poi` 自动导航分支绕过它——**挂点必须枚举全部执行路径**（本仓第三次应验，这次是本批自己挂漏） | 抽 `_remember_visited` 公共件挂两条路径，新增分支断言 |

**复验亮点（R1 = 全案样板）**：「导航去东方之门，路上买杯咖啡，五点前要到」一轮给出
——时限判定「预计13:25到达，比您要求的17:00早约215分钟」+ **真沿途**候选（歇马桥段咖啡店，
非目的地附近）+ 候选逐家 ETA「约13:36到（不迟到）」+ 记忆路线偏好自动叠加
「记得您平时不走高速」。B1 记忆链全通：「记住老婆喜欢粤菜不喜欢排队」→ 抽取
`taste.cuisine/subject=老婆/polarity=like` 落库 → 「和老婆吃饭」搜出粤菜馆
（本轮经 planner 读记忆填 cuisine 槽的软路径生效；确定性偏置层是兜底）。
A2 多途经保序+绕行量化、A3/A4 滴水湖与苏州大秋裤（城市约束赢了内建央视映射）、
A6 风景降档诚实话术、A7 动物园、A8 氛围软重排均一次通过。

**留档发现（未修，取证在案）**：
1. **目的地接地的就近包含误伤家族**（存量 R1 家族，本次一天三见）：「虹桥机场」→
   如家酒店停车场、「外滩」→星空艺术馆(百货8层)、「上次那个湖=滴水湖」→滴水湖雅悦
   酒店——`_dest_matches` 包含式校验 + near 距离序让「名字含目标词的近处 POI」顶掉
   本体。修它要动 R1 核心校验，需单独取证立卡（候选方向：交通枢纽/景区类目优先、
   短名精确优先、或「上次」场景直接用 episodic value 坐标不重搜）。
2. **B3「记住+未来时间」被 planner 直接路由成 reminder.create**（建了 8/15 周六 15:00
   的提醒，日期正确——探针作者一度误判差一天，实为 2026-08-14=周五）：这是比 G7 询问式
   **更好**的兑现形态；G7 针对的「不带记忆指令的顺嘴提及」在本轮语料未覆盖，机制仅有
   单测背书，真栈触发待后续语料补验。
3. 探针种入 u1 的测试数据已清（11 条记忆 + 1 条退化关系边「女儿-family-女儿」+
   钢琴比赛提醒 cancelled）；抽取端存量谓词别名散置（coffee.brand/consume.coffee/
   beverage.coffee 并存）是老账，不属本批。
