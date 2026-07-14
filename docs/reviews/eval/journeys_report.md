# 旅程级 e2e 报告（journeys_report）

- 生成时间：2026-07-15 00:26:35（耗时 1133.9s）
- active LLM：`mimo:mimo-v2.5-pro`（跨 provider 结果不可直接对比）
- 车道：all
- **回归级 13/13**（必须全绿）；目标级 7/18（红灯=工程 backlog）
- 时延（全轮）：P50=5.7s P95=40.0s max=62.8s n=65

## 记分卡

| 维度 | 通过 |
|---|---|
| autonomy | 13/18 |
| continuity | 11/20 |
| honesty | 3/3 |
| proactive | 2/4 |
| interaction | 3/6 |
| safety | 1/1 |

## 旅程明细

| id | 级别 | 结果 | 说明 |
|---|---|---|---|
| A1-1 单句三域并行：车控×媒体×天气 | regression | ✅ pass |  |
| A1-2 导航+沿途充电一句话（waypoint 并入 + 不重复导航） | regression | ✅ pass |  |
| A1-3 提醒+天气单句双意图 + 落库回读 + 自清理 | regression | ✅ pass |  |
| A2-2a 赛程→提醒跨域交接（REMINDABLE_ACTIVE 一轮成单） | regression | ⏭️ skip | 数据不可得（第1轮命中 ['没有查到', '暂无', '没有比赛', '无法获取', '拿不到', '处理失败']） |
| A2-3 低电量行程规划自动编织充电 | regression | ✅ pass |  |
| A3-1 时效问题不落陈旧直答：联网/改派/诚实三容忍 | regression | ✅ pass |  |
| A3-2 新闻→深挖第2条（NEWS_ACTIVE 桥接 + 重域过程区） | regression | ✅ pass |  |
| A4-1 异步深调研有始有终：受理→主动推送→续接深挖 | regression | ✅ pass |  |
| A4-2 分钟级提醒到点触达 + 卡按钮改期原条目 | regression | ✅ pass |  |
| A5-2 数据界外诚实不编造（免费档日期门） | regression | ⏭️ skip | 数据不可得（第1轮命中 ['处理失败']） |
| A5-3 多意图不稀释危险确认（后备箱+音乐） | regression | ✅ pass |  |
| B1-4 跨轮槽位继承：明天杭州→那后天呢 | regression | ✅ pass |  |
| B3-4 天气×出行联动意图先答（不反问目的地） | regression | ✅ pass |  |
| B4-1 快慢路径共享历史：端侧动作云端可忆 | regression | ✅ pass |  |
| B4-2 场景激活句不被端侧劫持 + custom_params 覆盖 | regression | ✅ pass |  |
| A1-4 条件依赖 DAG：查天气→按结果决定建不建提醒 | target | ❌ fail | any_of 全部分支未满足: speech_all 缺 ['雨'] | speech=好的，明早带伞。什么时候提醒你？ || speech_any 未命中 ['不会下雨', '无需', '不用带伞', '没有雨'] | speech=好的，明早带伞。什么时候提醒你？ |
| A2-1 搜店→导航单句直达（中间结果传递，不反问） | target | ✅ pass |  |
| A2-2b 赛程→提醒单句成单（REMINDABLE 单句版） | target | ✅ pass |  |
| A2-4 导航 ETA→提醒（REMINDABLE 即插契约的 navigation 缺口） | target | ❌ fail | speech_any 未命中 ['提醒'] | speech=暂不支持哦 |
| A3-3 搜索薄证据→follow_up 引导→「深入调研」续接同话题 | target | ✅ pass |  |
| A5-1 多步部分失败诚实回执（trip 故障注入，天气照答） | target | ✅ pass |  |
| B1-1 POI 列表→「就去第二家」跨域指代直达 | target | ✅ pass |  |
| B1-2 导航目的地→天气焦点迁移（那边≠当前定位） | target | ❌ fail | any_of 全部分支未满足: speech_any 未命中 ['盐田', '大梅沙'] | speech=深圳当前阴，气温26℃，体感29℃，东南风2级。 || card_contains 缺 ['盐田', '大梅沙'] |
| B1-3 导航目的地→「那附近」周边检索中心迁移 | target | ❌ fail | card_contains 缺 ['南山'] |
| B1-5 车控对象跨轮继承（副驾也开一下） | target | ✅ pass |  |
| B2-1 确认挂起+插话后仍可续接（场景创建） | target | ❌ fail | speech_any 未命中 ['下班模式'] | speech=当前没有待确认的操作。您可以重新告诉我需求。 |
| B2-2 补槽挂起+插话后裸答案仍可续接（reminder 双层挂起） | target | ✅ pass |  |
| B2-3 选择卡挂起+插话后「第一个」仍可回填（充电 dest_choice） | target | ❌ fail | cards_any 未命中 ['poi_list'] | 实际=['charging_route'] |
| B3-1 行程×天气反向驱动修改（哪天下雨改室内） | target | ❌ fail | any_of 全部分支未满足: speech_all 缺 ['室内'] | speech=已结合天气为您规划珠海2天行程：第1天（大雨 27-30℃）：长隆海洋王国、珠海横琴长隆国际海洋度假区 || speech_any 未命中 ['不用', '没有雨', '无需', '都没有雨'] | speech=已结合天气为您规 |
| B3-2 低电量长途导航主动补能建议（车辆接地护城河） | target | ❌ fail | any_of 全部分支未满足: speech_any 未命中 ['续航', '电量', '充电', '补能'] | speech=为您导航到广州仄仄科技有限公司（深南大道10128号南山数字文 || card_contains 缺 ['充电'] |
| B3-3 记忆×车控参数化（调到我喜欢的温度） | target | ❌ fail | 不该出现的动作 vehicle.control 出现了 |
| B5-1 「一次通勤」14 轮跨域长会话 showcase | target | ❌ fail | speech_any 未命中 ['周报'] | speech=好的，我刚才让你提醒我什么来着。什么时候提醒你？ |
| B5-2 列表叠加消歧：「第一个」指最新列表 | target | ❌ fail | cards_any 未命中 ['poi_list'] | 实际=['charging_route'] |

## 红灯清单（每条=一个待决策工作项）

### A1-4 条件依赖 DAG：查天气→按结果决定建不建提醒（target）
- 首损轮：1 `查一下明天深圳会不会下雨，要是下雨就提醒我明早带伞`
- 现象：any_of 全部分支未满足: speech_all 缺 ['雨'] | speech=好的，明早带伞。什么时候提醒你？ || speech_any 未命中 ['不会下雨', '无需', '不用带伞', '没有雨'] | speech=好的，明早带伞。什么时候提醒你？
- trace_id：`ebdcf619734844db`（dashboard 搜索直达）

### A2-4 导航 ETA→提醒（REMINDABLE 即插契约的 navigation 缺口）（target）
- 首损轮：2 `到之前一刻钟提醒我给张姐打电话`
- 现象：speech_any 未命中 ['提醒'] | speech=暂不支持哦
- trace_id：`45b1d4a8058c4d8d`（dashboard 搜索直达）

### B1-2 导航目的地→天气焦点迁移（那边≠当前定位）（target）
- 首损轮：2 `那边现在天气怎么样`
- 现象：any_of 全部分支未满足: speech_any 未命中 ['盐田', '大梅沙'] | speech=深圳当前阴，气温26℃，体感29℃，东南风2级。 || card_contains 缺 ['盐田', '大梅沙']
- trace_id：`988c6c3329824119`（dashboard 搜索直达）

### B1-3 导航目的地→「那附近」周边检索中心迁移（target）
- 首损轮：2 `那附近有停车场吗`
- 现象：card_contains 缺 ['南山']
- trace_id：`edc8852d4a6d4b71`（dashboard 搜索直达）

### B2-1 确认挂起+插话后仍可续接（场景创建）（target）
- 首损轮：3 `确认`
- 现象：speech_any 未命中 ['下班模式'] | speech=当前没有待确认的操作。您可以重新告诉我需求。; 全局禁词命中 '没有待确认的操作' | speech=当前没有待确认的操作。您可以重新告诉我需求。
- trace_id：`4217e09716944c0e`（dashboard 搜索直达）

### B2-3 选择卡挂起+插话后「第一个」仍可回填（充电 dest_choice）（target）
- 首损轮：1 `去惠州的路上帮我找个充电站`
- 现象：cards_any 未命中 ['poi_list'] | 实际=['charging_route']; card_contains 缺 ['dest_choice']
- trace_id：`7b03528371d54d06`（dashboard 搜索直达）

### B3-1 行程×天气反向驱动修改（哪天下雨改室内）（target）
- 首损轮：3 `哪天要下雨的话，把那天的安排换成室内的`
- 现象：any_of 全部分支未满足: speech_all 缺 ['室内'] | speech=已结合天气为您规划珠海2天行程：第1天（大雨 27-30℃）：长隆海洋王国、珠海横琴长隆国际海洋度假区 || speech_any 未命中 ['不用', '没有雨', '无需', '都没有雨'] | speech=已结合天气为您规划珠海2天行程：第1天（大雨 27-30
- trace_id：`1253487c1fb2475c`（dashboard 搜索直达）

### B3-2 低电量长途导航主动补能建议（车辆接地护城河）（target）
- 首损轮：1 `导航去广州塔`
- 现象：any_of 全部分支未满足: speech_any 未命中 ['续航', '电量', '充电', '补能'] | speech=为您导航到广州仄仄科技有限公司（深南大道10128号南山数字文 || card_contains 缺 ['充电']
- trace_id：`3d514816e36242a0`（dashboard 搜索直达）

### B3-3 记忆×车控参数化（调到我喜欢的温度）（target）
- 首损轮：1 `记住，我最喜欢的空调温度是26度`
- 现象：不该出现的动作 vehicle.control 出现了
- trace_id：`2997a333e35b4e11`（dashboard 搜索直达）

### B5-1 「一次通勤」14 轮跨域长会话 showcase（target）
- 首损轮：11 `我刚才让你提醒我什么来着`
- 现象：speech_any 未命中 ['周报'] | speech=好的，我刚才让你提醒我什么来着。什么时候提醒你？
- trace_id：`bbfb2efd05624987`（dashboard 搜索直达）

### B5-2 列表叠加消歧：「第一个」指最新列表（target）
- 首损轮：2 `去惠州的路上帮我找个充电站`
- 现象：cards_any 未命中 ['poi_list'] | 实际=['charging_route']; card_contains 缺 ['dest_choice']
- trace_id：`858347b66ead4815`（dashboard 搜索直达）

