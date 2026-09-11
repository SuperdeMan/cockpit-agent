# Android 四项体验修正：语音层手势与高度、控件高度统一、地图路线、桌面姿态

> 状态：落地中（2026-09-11，用户口述四条问题 → 本文定判据与代码位置 → 同批实施）
> 交付对象：mobile（React Native）、navigation / charging_planner Agent（路线几何）、`hmi/src/types.ts`（卡契约）
> 关联：[打磨批 A–G](2026-09-10-android-ui-polish-batches.md)、[AR03 横屏与停播](2026-09-08-ar03-stop-playback-landscape-implementation.md)、
> [AR04 浮动在场](2026-09-08-ar04-presentation-ack-implementation.md)、`mobile/README.md`、方案 §5.2 / §6 / §7.3

## 0. 四条问题（用户原话摘要）与裁决

| # | 用户看到的 | 根因（代码定位） | 裁决 |
|---|---|---|---|
| 1a | 语音层只能从顶缘把手带下拉收起，应整页任意位置下滑即收 | `VoiceSheet.tsx` 的 Pan **只挂在把手带**（B5-12：整层 Pan 会与层内 ScrollView 打架、吃掉 chips 横滑） | 整层 Pan + `Gesture.Native()` 包住 ScrollView 并声明 simultaneous；**只在滚动区处于顶部时**接管下拉，横向先动即失败（chips 横滑不受影响）；层跟手位移，松手按距离/速度判收起或回弹 |
| 1b | 上升幅度要按手机尺寸适配，否则同一状态在不同机器上「完成程度」不一样、光球可能被遮 | `sheetHeightDp` 泊车路径是**纯比例**（0.4/0.62/0.78 × 记录区高），没有内容下限；矮容器上 0.4 档装不下「把手带 + 球 + 胶囊」，球被裁 | 泊车路径补内容下限（与行车档同一函数、按泊车常量），`min(容器, max(比例, 下限))`。高屏读数逐 dp 不变（下限低于比例），矮屏由下限托住 |
| 2 | 可点的胶囊 / 按钮高度不一致 | 全仓 22 / 26 / 30 / 36 / 38 / 44 / 48 六种高度并存：状态胶囊 26、欢迎推荐 38、卡内「地图」26、「看菜单」「导航」22、地图页「全览」30、卡内按钮 44、设置页多处 44 | 两档制：**按钮 = 触控目标**（泊车 48 / 行车 56，已有 `TARGET`）；**胶囊/chip = `PILL`**（泊车 36 / 行车 44 视觉高），外框仍撑到触控目标。新增 `ui/Pill.tsx` 一个组件承载后者，全部胶囊类站点改用它 |
| 3 | 「导航去 xxx」没有进地图的入口，途经路线没画出来；平板/展开态应利用舞台 | `route_plan` / `charging_route` 卡契约**没有坐标**（M3-3 挂账「折线等后端补」）；地图页只画点不画线；舞台 `map` 场景只是把卡再渲一遍 | 后端：`route_plan` 卡补 `origin_loc` / `destination_loc` / `waypoints[].lat,lng` / `path`（抽样 ≤ 240 点），`charging_route` 的 stops 补坐标。客户端：`core/map/geometry.ts` 一份「这张卡能不能画」判据；地图页画折线 + 角色标注；舞台 `map` 场景内嵌地图（双栏 / 抽屉 / 桌面）|
| 4 | 折叠屏 90° 桌面姿态，上半部分三个状态数值被截断 | tabletop 上半 = `StagePane` 竖着堆「模式行 + 120 球 + 车况三格 + 提醒 + 场景卡」，OPPO 内屏上半只有约 246dp（铰链上缘 − 顶栏），球之后剩不到 20dp，三格被 `overflow: hidden` 裁掉 | 上半改**横排**：左列光球（上半 ≥ 200dp 用 120，否则 88）、右列可滚（车况三格 / 提醒 / 场景）。判据 `tabletopStage()` 进 `sizeClass.ts` |

## 1. 语音层

### 1.1 整层下滑收起（`VoiceSheet.tsx`）

- 手势结构：外层 `Gesture.Pan().activeOffsetY(12).failOffsetX([-16, 16]).simultaneousWithExternalGesture(scroll)`，`scroll = Gesture.Native()` 包住内容 ScrollView。
- 接管条件：手势开始时滚动偏移 ≤ 1dp（`onScroll` 记录）。不在顶部 ⇒ 这次拖动完全交给 ScrollView（`ignore`），用户抬手再拉一次即可。
- 跟手：`dragY = max(0, translationY)` 作 `translateY`；松手按 `sheetDragOutcome()` 判：
  - `dismiss`：`translationY > 80` **或**（`translationY > 24` 且 `velocityY > 700dp/s`）；
  - `settle`：回弹到 0（spring）。
- 把手带保留轻点收起、暗区点按收起、返回键三条既有出口不变；层内停止键 / chips / 卡内按钮照旧可点（Pan 在 12dp 位移之前不激活）。
- 判据文件：`ui/layout/sheetGesture.ts`（纯函数，`test/sheetGesture.test.ts`）。

### 1.2 高度下限扩到泊车（`ui/layout/sheetHeight.ts`）

泊车下限 = 把手带（48）+ 内容区 padding 32 + 球列（88 + 12 + 胶囊行 20）+ 该档主体：

| 档 | 主体 | 泊车下限（normal） |
|---|---|---|
| 0.4 | 只有球列 | 200 |
| 0.62 | 回答两行（16pt / 24） | 260 |
| 0.78 | 回答两行 + gap 12 + 卡头（CardShell 边框 2 + padding 24 + 类型行 16） | 314 |

外屏竖实测记录区 578.67dp 上三档比例（231 / 359 / 451）都高于下限 ⇒ **主力机读数逐 dp 不变**；矮容器（如 ≤ 500dp）才由下限托住，且仍 clamp 到容器。

## 2. 控件高度两档制

| 档 | 数值（normal 字号） | 谁 |
|---|---|---|
| 按钮 `TARGET` | 泊车 48 / 行车 56 | Dock 确认/取消、卡内按钮排、设置页按钮、隐私栏按钮、发送键、顶栏图标钮、停止播报 |
| 胶囊 `PILL` | 视觉 36 / 44，外框 ≥ `TARGET` | 状态胶囊、追问 chips（层内 + Composer）、欢迎推荐、支持页浮动动作键、「回到最新」、卡内「地图」「看菜单」「导航」、商户分类 / 规格 chips、设置页单选项与首页示例 chips、地图页「全览」 |

`Pill` 组件：`Pressable`（testID / 无障碍 / 热区都在它上）`minHeight = scale(TARGET)`，内层药丸 `height = scale(PILL)`。探针（`target_probe`）量的是 Pressable 的视觉 bounds，仍 ≥ 48 / 56。

## 3. 地图路线

### 3.1 契约（`hmi/src/types.ts`，全部可选、向后兼容）

```ts
RoutePlanCard: origin_loc?, destination_loc?: { lat, lng }; waypoints[].lat?/lng?; path?: Array<[lat, lng]>
ChargingRouteCard: origin_loc?, destination_loc?; stops[].lat?/lng?; path?
```

### 3.2 后端

- `providers/amap.py::get_route(with_polyline=True)` 额外返回 `path`（各 step 的 `polyline` 串接、抽样 ≤ 240 点）；`points`（每步终点 + 累计里程）保持不变。
- `agent.py` 三条出 `route_plan` 卡的路径（普通导航 `_route_plan_to`、途经点 `_navigate_via_waypoint`、只算不导 `_estimate`）改 `with_polyline=True`，卡上带几何；provider 没给几何时字段不出现。
- `charging_planner` 两处 `charging_route` 卡：stops 补 `lat/lng`；`plan_route` 的路线点作 `path`。

### 3.3 客户端

- `core/map/geometry.ts::cardGeometry(card)`：唯一判据——route_plan / charging_route / trip_itinerary / poi_detail / place_list / place_detail 各自给出 `{ title, points(带 role), path }` 或 `null`。「地图」入口只在它非空 ∧ `MAP_AVAILABLE` 时出现（M3-3 的三条件不变）。
- 地图页：`Polyline` 画 `path`；起点 / 途经 / 终点 / 补电站 / POI 五种角色分色标注；信息条标题「起点 → 终点」。
- 舞台：`scene.kind === 'map'` 时先渲内嵌地图（双栏 260dp / 桌面与抽屉 160dp），再渲卡；点地图或「打开地图」进整页。

## 4. 桌面姿态

`StagePane` 收到 `orb` 时改横排（判据 `sizeClass.ts::tabletopStage(topDp)`：`orb = topDp ≥ 200 ? 120 : 88`），`stage-mode` 行仍在最上，`stage-pane` ScrollView 是右列。`ChatScreen` 把 `tabletopSplit` 算出的上半高传下去。

## 5. 验收

- jest：`sheetGesture` / `sheetHeight`（泊车下限逐项）/ `pill`（外框 ≥ 目标、视觉 = PILL、行车档）/ `mapGeometry` / `stagePane`（桌面横排 + 地图场景）/ 既有 `composerChips` `chatHierarchy` `assistantPresence` 改为读外框。
- python：`agents/navigation`（provider path 抽样、三条卡路径带几何）、`agents/charging_planner`。
- hmi：`npm test`（共享类型只加可选字段）。
- 真机：prod 包装 OPPO；语音层深链升起后整层下滑收起、chips 横滑照旧；欢迎推荐 / 胶囊 / 设置页 chips 同高；卡片画廊 `route_plan（带路线几何）` → 地图页折线 + 三色标注。桌面姿态与云端真实路线卡分别依赖折叠机与部署，本批只到本地证据。

## 6. 风险与边界

- 云端未部署前，真实 `route_plan` 卡没有坐标 ⇒ 客户端行为与今天一致（无入口），不会出现点了没反应的按钮。
- 整层 Pan 与 ScrollView simultaneous 在 Android 上的实际手感需真机验；若滚动区不在顶部时出现「层跟着动」，判据里的 `atTop` 就是要看的那一个变量。
- `PILL` 视觉 36 让 chips 行少占 12dp（P28 的竖向预算受益），但状态胶囊外框从 34 → 48 行，对话页竖向多 14dp。

## 7. 实施记录（2026-09-11）

### 7.1 改了什么（基线 `d03c9e6`，本批尚未 commit）

| 面 | 文件 | 内容 |
|---|---|---|
| 语音层手势 | `mobile/src/ui/layout/sheetGesture.ts`（新）、`features/chat/VoiceSheet.tsx` | 整层 Pan + `Gesture.Native()` 包滚动区（simultaneous）；`atTop` / 跟手 `translateY` / 松手 `sheetDragOutcome`；把手带只留轻点；`overScrollMode="never"` |
| 语音层高度 | `ui/layout/sheetHeight.ts` | 新 `parkedSheetMinDp`；`sheetHeightDp` 行车 / 泊车同一条 `min(容器, max(比例, 下限))` |
| 控件两档制 | `ui/tokens.ts`（`PILL`）、`ui/theme.ts`（`Palette.target()`）、`ui/Pill.tsx`（新） | 胶囊类站点全部改 `Pill`：FollowUpChips、Composer chips、欢迎推荐、PresenceCapsule、支持页动作键、回到最新、卡内「查看路线 / 地图」「看菜单」「导航」、商户分类 / 规格 chips、设置页单选项 / 首页示例 / 试听、引导页预设、地图页「全览 / 收起」。按钮类 44 → `p.target(TARGET.parked)`：卡内按钮排、意图澄清项、场景行、商户两枚键、气泡里的过程折叠 / 重发 / 追问、车辆页「其他」、设置页各处 |
| 地图路线（后端） | `agents/navigation/src/route_geometry.py`（新）、`providers/amap.py`（`with_polyline` 多返回 `path`）、`providers/mock.py`、`agent.py`（`_estimate` / `_navigate_via_waypoint` / `_route_plan_to` 三处 `with_polyline=True` + `card_geometry`）、`agents/charging_planner`（`ChargingPlan.path/origin_loc`、stops 坐标、两处卡）、`hmi/src/types.ts`（可选字段） | 卡上只写拿得到的键；provider 缺几何 ⇒ 与今天逐键相同 |
| 地图路线（客户端） | `core/map/available.ts`（`MapRole`）、`core/map/geometry.ts`（新）、`features/map/{MapLayers,StageMap,amapInit}.tsx`（新）、`app/map.tsx`、`features/cards/navCards.tsx`、`features/stage/StagePane.tsx`、`ui/layout/sizeClass.ts`（`STAGE_MAP_HEIGHT`）、`types/react-native-amap3d.d.ts`、`features/cards/fixtures.ts`（带几何样本） | 高德 SDK `Polyline` 画折线、`Marker` 自定义 view 画「起 / 经 / 终 / 电 / 序号」；route_plan / charging_route / trip_itinerary 三种卡新增入口 |
| 桌面姿态 | `ui/layout/sizeClass.ts`（`tabletopStage`）、`StagePane.tsx`、`ChatScreen.tsx`（传 `topHeight`） | 上半横排：左列球（≥200dp 用 120，否则 88）、右列可滚 |

### 7.2 本地验证（工作树 = `d03c9e6` + 本批未提交改动）

| 口径 | 结果 |
|---|---|
| mobile `npx jest` | **999 passed / 93 suites**（基线 962）；新增 `sheetGesture` / `pill` / `mapGeometry` / `stagePane` 四个文件，`sheetHeight` 泊车下限逐项、`sizeClass` 桌面球径与舞台地图高、`tokens` 的 `PILL`；`composerChips` / `chatHierarchy` 的计数改为取 Pill 的外框 Pressable（react-test-renderer 把 Pill 组件与外框都当实例，`findAllByType(Pressable)` 对 memo 组件取不到） |
| mobile `tsc --noEmit` / `eslint --max-warnings 0` | 0 / 0 |
| Python 全量固定口径（`TZ=UTC0 -n 8 --dist worksteal`） | **8243 passed / 32 skipped / 14 warnings**，618s，rc=0（基线 8234 / 32 / 13；+9 = navigation 7 + charging 2；多出的一条 warning 是 `scripts/tests/test_e2e_target.py:449` 的正则 FutureWarning，该文件本批未动） |
| hmi `npm test` | 333 / 333 |
| 首轮全量 jest 里 `assistantPresence` 两条红 | 与 Python 全量同时跑时超时；单跑 12/12、错开后全量 999/999 ⇒ 资源争用不是缺陷 |

反向验证（各改一处、只跑该文件、看红、再按字节恢复——五处恢复后 `==` 原文均为 True）：`sheetDragOutcome` 去掉 `atTop` 判定 ⇒ `sheetGesture` 1/6 红；`parkedSheetMinDp` 的 0.62 主体改成 1 行 ⇒ `sheetHeight` 2/20 红；`Pill` 外框 `minHeight` 改 44 ⇒ `pill` + `composerChips` 3/11 红；`cardGeometry` 的 route_plan 分支不读 `destination_loc` ⇒ `mapGeometry` + `stagePane` 2/18 红；`route_path_from_amap` 不翻转 lng/lat ⇒ `test_route_geometry` 3/7 红。

### 7.3 真机与云端（回填）

- prod 包：见下方回填。
- 云端：**未 push、未 deploy**。真实 `route_plan` / `charging_route` 卡的几何要等含 navigation / charging_planner 改动的 release 上生产；之前客户端对真实卡的行为与今天逐字一致（无入口、无地图）。
