# 旅程级 e2e 报告（journeys_report）

- 生成时间：2026-07-15 12:21:01（耗时 1136.1s）
- active LLM：`mimo:mimo-v2.5-pro`（跨 provider 结果不可直接对比）
- 车道：all
- **回归级 14/15**（必须全绿）；目标级 14/18（红灯=工程 backlog）
- 时延（全轮）：P50=5.2s P95=36.0s max=52.8s n=72

## 记分卡

| 维度 | 通过 |
|---|---|
| autonomy | 18/19 |
| continuity | 16/21 |
| honesty | 4/4 |
| proactive | 4/4 |
| interaction | 3/6 |
| safety | 1/1 |

## 旅程明细

| id | 级别 | 结果 | 说明 |
|---|---|---|---|
| A1-1 单句三域并行：车控×媒体×天气 | regression | ✅ pass |  |
| A1-2 导航+沿途充电一句话（waypoint 并入 + 不重复导航） | regression | ✅ pass |  |
| A1-3 提醒+天气单句双意图 + 落库回读 + 自清理 | regression | ✅ pass |  |
| A2-2a 赛程→提醒跨域交接（REMINDABLE_ACTIVE 一轮成单） | regression | ✅ pass |  |
| A2-3 低电量行程规划自动编织充电 | regression | ✅ pass |  |
| A3-1 时效问题不落陈旧直答：联网/改派/诚实三容忍 | regression | ✅ pass |  |
| A3-2 新闻→深挖第2条（NEWS_ACTIVE 桥接 + 重域过程区） | regression | ✅ pass |  |
| A4-1 异步深调研有始有终：受理→主动推送→续接深挖 | regression | ✅ pass |  |
| A4-2 分钟级提醒到点触达 + 卡按钮改期原条目 | regression | ✅ pass |  |
| A5-2 数据界外诚实不编造（免费档日期门） | regression | ✅ pass |  |
| A5-3 多意图不稀释危险确认（后备箱+音乐） | regression | ✅ pass |  |
| B1-4 跨轮槽位继承：明天杭州→那后天呢 | regression | ✅ pass |  |
| B3-4 天气×出行联动意图先答（不反问目的地） | regression | ✅ pass |  |
| B4-1 快慢路径共享历史：端侧动作云端可忆 | regression | ❌ fail | speech_any 未命中 ['15', '十五'] | speech=抱歉，我刚才好像没记下来，可能是我走神了。为了不打扰你开车，这次就不回头翻了，辛苦你再确认一下或者重新设一下吧。 |
| B4-2 场景激活句不被端侧劫持 + custom_params 覆盖 | regression | ✅ pass |  |
| A1-4 条件依赖 DAG：查天气→按结果决定建不建提醒 | target | ✅ pass |  |
| A2-1 搜店→导航单句直达（中间结果传递，不反问） | target | ✅ pass |  |
| A2-2b 赛程→提醒单句成单（REMINDABLE 单句版） | target | ✅ pass |  |
| A2-4 导航 ETA→提醒（REMINDABLE 即插契约的 navigation 缺口） | target | ✅ pass |  |
| A3-3 搜索薄证据→follow_up 引导→「深入调研」续接同话题 | target | ✅ pass |  |
| A5-1 多步部分失败诚实回执（trip 故障注入，天气照答） | target | ✅ pass |  |
| B1-1 POI 列表→「就去第二家」跨域指代直达 | target | ❌ fail | action 未命中 {'type': 'navigate'} | 实际类型=[] |
| B1-2 导航目的地→天气焦点迁移（那边≠当前定位） | target | ✅ pass |  |
| B1-3 导航目的地→「那附近」周边检索中心迁移 | target | ✅ pass |  |
| B1-5 车控对象跨轮继承（副驾也开一下） | target | ✅ pass |  |
| B2-1 确认挂起+插话后仍可续接（场景创建） | target | ✅ pass |  |
| B2-2 补槽挂起+插话后裸答案仍可续接（reminder 双层挂起） | target | ✅ pass |  |
| B2-3 选择卡挂起+插话后「第一个」仍可回填（充电 dest_choice） | target | ✅ pass |  |
| B3-1 行程×天气反向驱动修改（哪天下雨改室内） | target | ✅ pass |  |
| B3-2 低电量长途导航主动补能建议（车辆接地护城河） | target | ✅ pass |  |
| B3-3 记忆×车控参数化（调到我喜欢的温度） | target | ❌ fail | 终态车况: hvac_temp=22 期望 26 |
| B5-1 「一次通勤」14 轮跨域长会话 showcase | target | ❌ fail | any_of 全部分支未满足: speech_any 未命中 ['取消'] | speech=好的，交周报。什么时候提醒你？ || speech_any 未命中 ['没有找到', '没有提醒'] | speech=好的，交周报。什么时候提醒你？ |
| B5-2 列表叠加消歧：「第一个」指最新列表 | target | ❌ fail | card_contains 缺 ['dest_choice'] |

## 红灯清单（每条=一个待决策工作项）

### B4-1 快慢路径共享历史：端侧动作云端可忆（regression）
- 首损轮：2 `我刚才让你把音量调到多少`
- 现象：speech_any 未命中 ['15', '十五'] | speech=抱歉，我刚才好像没记下来，可能是我走神了。为了不打扰你开车，这次就不回头翻了，辛苦你再确认一下或者重新设一下吧。
- trace_id：`122f089000dc44e2`（dashboard 搜索直达）

### B1-1 POI 列表→「就去第二家」跨域指代直达（target）
- 首损轮：2 `就去第二家`
- 现象：action 未命中 {'type': 'navigate'} | 实际类型=[]
- trace_id：`60537dec3aef4d73`（dashboard 搜索直达）

### B3-3 记忆×车控参数化（调到我喜欢的温度）（target）
- 首损轮：final ``
- 现象：终态车况: hvac_temp=22 期望 26
- trace_id：``（dashboard 搜索直达）

### B5-1 「一次通勤」14 轮跨域长会话 showcase（target）
- 首损轮：12 `那个提醒不用了，取消吧`
- 现象：any_of 全部分支未满足: speech_any 未命中 ['取消'] | speech=好的，交周报。什么时候提醒你？ || speech_any 未命中 ['没有找到', '没有提醒'] | speech=好的，交周报。什么时候提醒你？
- trace_id：`7b14140e2f6a46a7`（dashboard 搜索直达）

### B5-2 列表叠加消歧：「第一个」指最新列表（target）
- 首损轮：2 `去惠州的路上帮我找个充电站`
- 现象：card_contains 缺 ['dest_choice']
- trace_id：`91b324462dfc4e90`（dashboard 搜索直达）

