# 旅程级 e2e 报告（journeys_report）

- 生成时间：2026-07-25 11:47:07（耗时 1023.5s）
- active LLM：`minimax:MiniMax-M3`（跨 provider 结果不可直接对比）
- 车道：all
- **回归级 12/14**（必须全绿）；目标级 15/18（红灯=工程 backlog）
- 时延（全轮）：P50=5.4s P95=19.1s max=66.7s n=68

## 记分卡

| 维度 | 通过 |
|---|---|
| autonomy | 17/18 |
| continuity | 17/20 |
| honesty | 3/4 |
| proactive | 3/4 |
| interaction | 3/6 |
| safety | 1/1 |

## 旅程明细

| id | 级别 | 结果 | 说明 |
|---|---|---|---|
| A1-1 单句三域并行：车控×媒体×天气 | regression | ✅ pass |  |
| A1-2 导航+沿途充电一句话（waypoint 并入 + 不重复导航） | regression | ✅ pass |  |
| A1-3 提醒+天气单句双意图 + 落库回读 + 自清理 | regression | ✅ pass |  |
| A2-2a 赛程→提醒跨域交接（REMINDABLE_ACTIVE 一轮成单） | regression | ⏭️ skip | 数据不可得（第1轮命中 ['没有查到', '没有查询到', '暂无', '没有比赛', '无法获取', '拿不到', '处理失败']） |
| A2-3 低电量行程规划自动编织充电 | regression | ✅ pass |  |
| A3-1 时效问题不落陈旧直答：联网/改派/诚实三容忍 | regression | ❌ fail | any_of 全部分支未满足: cards_any 未命中 ['search_result', 'sports_scores', 'news_brief'] | 实际=[] || speech_any 未命中 ['没有', '未找到', '查不到', '无法', '没查到', '不确定', '暂无', '并没有', ' |
| A3-2 新闻→深挖第2条（NEWS_ACTIVE 桥接 + 重域过程区） | regression | ✅ pass |  |
| A4-1 异步深调研有始有终：受理→主动推送→续接深挖 | regression | ✅ pass |  |
| A4-2 分钟级提醒到点触达 + 卡按钮改期原条目 | regression | ❌ fail | 收帧超时（final 未到） |
| A5-2 数据界外诚实不编造（免费档日期门） | regression | ✅ pass |  |
| A5-3 多意图不稀释危险确认（后备箱+音乐） | regression | ✅ pass |  |
| B1-4 跨轮槽位继承：明天杭州→那后天呢 | regression | ✅ pass |  |
| B3-4 天气×出行联动意图先答（不反问目的地） | regression | ✅ pass |  |
| B4-1 快慢路径共享历史：端侧动作云端可忆 | regression | ✅ pass |  |
| B4-2 场景激活句不被端侧劫持 + custom_params 覆盖 | regression | ✅ pass |  |
| A1-4 条件依赖 DAG：查天气→按结果决定建不建提醒 | target | ✅ pass |  |
| A2-1 搜店→导航单句直达（中间结果传递，不反问） | target | ✅ pass |  |
| A2-2b 赛程→提醒单句成单（REMINDABLE 单句版） | target | ✅ pass |  |
| A2-4 导航 ETA→提醒（REMINDABLE 即插契约的 navigation 缺口） | target | ✅ pass |  |
| A3-3 搜索薄证据→follow_up 引导→「深入调研」续接同话题 | target | ✅ pass |  |
| A5-1 多步部分失败诚实回执（trip 故障注入，天气照答） | target | ✅ pass |  |
| B1-1 POI 列表→「就去第二家」跨域指代直达 | target | ❌ fail | action 未命中 {'type': 'navigate'} | 实际类型=[] |
| B1-2 导航目的地→天气焦点迁移（那边≠当前定位） | target | ❌ fail | any_of 全部分支未满足: speech_any 未命中 ['盐田', '大梅沙'] | speech=抱歉，处理失败。 || card_contains 缺 ['盐田', '大梅沙'] |
| B1-3 导航目的地→「那附近」周边检索中心迁移 | target | ✅ pass |  |
| B1-5 车控对象跨轮继承（副驾也开一下） | target | ✅ pass |  |
| B2-1 确认挂起+插话后仍可续接（场景创建） | target | ✅ pass |  |
| B2-2 补槽挂起+插话后裸答案仍可续接（reminder 双层挂起） | target | ✅ pass |  |
| B2-3 选择卡挂起+插话后「第一个」仍可回填（充电 dest_choice） | target | ❌ fail | cards_any 未命中 ['poi_list'] | 实际=['place_list'] |
| B3-1 行程×天气反向驱动修改（哪天下雨改室内） | target | ✅ pass |  |
| B3-2 低电量长途导航主动补能建议（车辆接地护城河） | target | ✅ pass |  |
| B3-3 记忆×车控参数化（调到我喜欢的温度） | target | ✅ pass |  |
| B5-1 「一次通勤」14 轮跨域长会话 showcase | target | ✅ pass |  |
| B5-2 列表叠加消歧：「第一个」指最新列表 | target | ✅ pass |  |

## 红灯清单（每条=一个待决策工作项）

### A3-1 时效问题不落陈旧直答：联网/改派/诚实三容忍（regression）
- 首损轮：1 `昨晚欧冠决赛的比分是多少`
- 现象：any_of 全部分支未满足: cards_any 未命中 ['search_result', 'sports_scores', 'news_brief'] | 实际=[] || speech_any 未命中 ['没有', '未找到', '查不到', '无法', '没查到', '不确定', '暂无', '并没有', '失败'] | spe
- trace_id：`7ae753e6af2b436d`（dashboard 搜索直达）

### A4-2 分钟级提醒到点触达 + 卡按钮改期原条目（regression）
- 首损轮：3 ``
- 现象：收帧超时（final 未到）
- trace_id：``（dashboard 搜索直达）

### B1-1 POI 列表→「就去第二家」跨域指代直达（target）
- 首损轮：2 `就去第二家`
- 现象：action 未命中 {'type': 'navigate'} | 实际类型=[]
- trace_id：`3aa53b043d1d48a3`（dashboard 搜索直达）

### B1-2 导航目的地→天气焦点迁移（那边≠当前定位）（target）
- 首损轮：2 `那边现在天气怎么样`
- 现象：any_of 全部分支未满足: speech_any 未命中 ['盐田', '大梅沙'] | speech=抱歉，处理失败。 || card_contains 缺 ['盐田', '大梅沙']
- trace_id：`94346b0bd4bd435f`（dashboard 搜索直达）

### B2-3 选择卡挂起+插话后「第一个」仍可回填（充电 dest_choice）（target）
- 首损轮：1 `去惠州怎么充电`
- 现象：cards_any 未命中 ['poi_list'] | 实际=['place_list']; card_contains 缺 ['dest_choice']
- trace_id：`44525c4501c24d31`（dashboard 搜索直达）

