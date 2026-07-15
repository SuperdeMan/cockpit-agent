# 旅程级 e2e 报告（journeys_report）

- 生成时间：2026-07-15 15:51:31（耗时 881.7s）
- active LLM：`minimax:MiniMax-M3`（跨 provider 结果不可直接对比）
- 车道：all
- **回归级 15/15**（必须全绿）；目标级 13/18（红灯=工程 backlog）
- 时延（全轮）：P50=4.9s P95=25.5s max=39.5s n=69

## 记分卡

| 维度 | 通过 |
|---|---|
| autonomy | 16/19 |
| continuity | 18/21 |
| honesty | 4/4 |
| proactive | 4/4 |
| interaction | 4/6 |
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
| B4-1 快慢路径共享历史：端侧动作云端可忆 | regression | ✅ pass |  |
| B4-2 场景激活句不被端侧劫持 + custom_params 覆盖 | regression | ✅ pass |  |
| A1-4 条件依赖 DAG：查天气→按结果决定建不建提醒 | target | ❌ fail | any_of 全部分支未满足: speech_all 缺 ['提醒'] | speech=今天有雨，出门记得带伞。深圳当前大雨，气温25℃，体感28℃，西南风2级。 || speech_any 未命中 ['不会下雨', '无需', '不用带伞', '没有雨'] | speech=今天有雨，出门记得带伞。深圳当前大雨，气 |
| A2-1 搜店→导航单句直达（中间结果传递，不反问） | target | ❌ fail | speech_not 命中禁词 '第几个' | speech=给您推荐灯花·川小馆(赋安科技大厦B座店)，10家里评分最高，4.7分，人均94元，路线已经规划好了，可以直接出发。另外 |
| A2-2b 赛程→提醒单句成单（REMINDABLE 单句版） | target | ✅ pass |  |
| A2-4 导航 ETA→提醒（REMINDABLE 即插契约的 navigation 缺口） | target | ✅ pass |  |
| A3-3 搜索薄证据→follow_up 引导→「深入调研」续接同话题 | target | ✅ pass |  |
| A5-1 多步部分失败诚实回执（trip 故障注入，天气照答） | target | ✅ pass |  |
| B1-1 POI 列表→「就去第二家」跨域指代直达 | target | ✅ pass |  |
| B1-2 导航目的地→天气焦点迁移（那边≠当前定位） | target | ✅ pass |  |
| B1-3 导航目的地→「那附近」周边检索中心迁移 | target | ✅ pass |  |
| B1-5 车控对象跨轮继承（副驾也开一下） | target | ✅ pass |  |
| B2-1 确认挂起+插话后仍可续接（场景创建） | target | ✅ pass |  |
| B2-2 补槽挂起+插话后裸答案仍可续接（reminder 双层挂起） | target | ❌ fail | any_of 全部分支未满足: speech_any 未命中 ['什么时候', '几点'] | speech=记下了：吃降压药。办完了跟我说「完成」就行。 || follow_up_any 未命中 ['什么时候', '几点'] |  |
| B2-3 选择卡挂起+插话后「第一个」仍可回填（充电 dest_choice） | target | ✅ pass |  |
| B3-1 行程×天气反向驱动修改（哪天下雨改室内） | target | ✅ pass |  |
| B3-2 低电量长途导航主动补能建议（车辆接地护城河） | target | ✅ pass |  |
| B3-3 记忆×车控参数化（调到我喜欢的温度） | target | ✅ pass |  |
| B5-1 「一次通勤」14 轮跨域长会话 showcase | target | ❌ fail | any_of 全部分支未满足: speech_any 未命中 ['取消'] | speech=「世界杯第一场是谁踢？开赛前提醒我」已完成。 || speech_any 未命中 ['没有找到', '没有提醒'] | speech=「世界杯第一场是谁踢？开赛前提醒我」已完成。 |
| B5-2 列表叠加消歧：「第一个」指最新列表 | target | ❌ fail | speech_any 未命中 ['充电'] | speech=您想看哪一家的详情？ |

## 红灯清单（每条=一个待决策工作项）

### A1-4 条件依赖 DAG：查天气→按结果决定建不建提醒（target）
- 首损轮：1 `查一下明天深圳会不会下雨，要是下雨就提醒我明早带伞`
- 现象：any_of 全部分支未满足: speech_all 缺 ['提醒'] | speech=今天有雨，出门记得带伞。深圳当前大雨，气温25℃，体感28℃，西南风2级。 || speech_any 未命中 ['不会下雨', '无需', '不用带伞', '没有雨'] | speech=今天有雨，出门记得带伞。深圳当前大雨，气温25℃，体
- trace_id：`0c52792836ab45ed`（dashboard 搜索直达）

### A2-1 搜店→导航单句直达（中间结果传递，不反问）（target）
- 首损轮：1 `找一家附近评分最高的川菜馆，直接导航过去`
- 现象：speech_not 命中禁词 '第几个' | speech=给您推荐灯花·川小馆(赋安科技大厦B座店)，10家里评分最高，4.7分，人均94元，路线已经规划好了，可以直接出发。另外
- trace_id：`94fcc92e54094129`（dashboard 搜索直达）

### B2-2 补槽挂起+插话后裸答案仍可续接（reminder 双层挂起）（target）
- 首损轮：1 `提醒我吃降压药`
- 现象：any_of 全部分支未满足: speech_any 未命中 ['什么时候', '几点'] | speech=记下了：吃降压药。办完了跟我说「完成」就行。 || follow_up_any 未命中 ['什么时候', '几点'] | 
- trace_id：`d555c329f59c4eed`（dashboard 搜索直达）

### B5-1 「一次通勤」14 轮跨域长会话 showcase（target）
- 首损轮：12 `那个提醒不用了，取消吧`
- 现象：any_of 全部分支未满足: speech_any 未命中 ['取消'] | speech=「世界杯第一场是谁踢？开赛前提醒我」已完成。 || speech_any 未命中 ['没有找到', '没有提醒'] | speech=「世界杯第一场是谁踢？开赛前提醒我」已完成。
- trace_id：`3f5a9826cb6d4955`（dashboard 搜索直达）

### B5-2 列表叠加消歧：「第一个」指最新列表（target）
- 首损轮：3 `看看第一个的详情`
- 现象：speech_any 未命中 ['充电'] | speech=您想看哪一家的详情？
- trace_id：`bf3b144b3484459c`（dashboard 搜索直达）

