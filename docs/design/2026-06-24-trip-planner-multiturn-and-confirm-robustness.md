# 行程规划多轮闭环 + 确认健壮性 + 跨 Agent 上下文透传（2026-06-24）

> 落地记录。本轮把「去X几天带老人…顺便看天气/充电」这类复合出行请求的完整链路
> （规划 → 改某天 → 确认 → 第一站导航）做成稳定闭环，并修掉几个跨组件根因。
> 受众：维护本仓库的人/AI。涉及 `orchestrator/cloud/planning.py`、`orchestrator/cloud/engine.py`、
> `agents/trip_planner/`、`agents/_sdk/`、`agents/charging_planner/`、`orchestrator/edge/server.py`、`hmi/`。

## 背景

trip-planner 早期只有 `trip.plan` + `trip.modify` 两个意图，靠 LLM Planner（MiMo）路由。
实测弱模型在以下点反复翻车，且多属**跨组件**（改一处不够）：

1. 复合句「周末去北京三天带老人…顺便看天气/充电」**偶发不出行程规划**，只回天气/充电。
2. 「第二天行程换一个」**没被识别成改行程**，反而被当确认 / 误路由成充电导航。
3. 确认后**陷入循环**（确认→又改一遍→再确认）。
4. 复合 Agent（trip-planner 内部调 navigation/charging）**拿不到当前定位/真实电量**。
5. 确认后第一站**用泛搜景点**，而不是行程里第一天的地点。

## 改动分区

### 1. 确定性出行兜底（planning.py，覆盖解析+降级两条路径）

弱 LLM 三层 prompt 强化都不稳定，改为**确定性后处理**，放在 `PlanBuilder.build()` 末端——
对**最终 Plan** 生效，无论计划来自 LLM 正常解析还是解析失败降级到语义路由（`_fallback` 的
top-1 常只命中天气/充电）。这是关键：早期注入只写在 `_parse_and_validate` 内，**降级路径绕过了它**，
导致 1/5 概率掉行程。

- `_ensure_trip_step(plan, text, agent_map)`：命中出行模式（`去/到X` + `N天/N日游/带老人不要太累/行程/自驾游/度假`）
  且 trip-planner 可用、计划里无 `trip.plan` → 追加并列 `trip.plan` 步。目的地/天数/偏好用正则解析
  （`_extract_trip`），通勤/固定点（公司/家/机场/车站，**前缀**判定，避免"张家界"被单字"家"误杀）不触发。
- `_ensure_trip_modify(plan, text, agent_map)`：命中修改模式（`第N天…换/改/调整`、`行程/景点…换/改`、`换个景点`）
  → 用**单步 trip.modify 取代**误规划（修改不需要重跑天气/充电）。返回 True 时 `build()` 跳过 `_ensure_trip_step`（二者互斥）。
- 两个兜底都复用 `_validated_steps` 装配 endpoint/权限/budget，与正常步骤同一路径。

### 2. trip-planner 多轮有状态（agents/trip_planner/src/agent.py）

trip-planner 原本无状态，导致 `trip.modify` 只拿到 modification 槽位、不知道原行程，瞎编占位。
新增**会话级内存缓存** `self._sessions[session_id] = {destination, days, itinerary, pois, first_stop}`
（PoC 单实例内存态，量产应落 memory 服务）：

- `_plan` 生成行程后 `_remember(...)`，并解析第一天主要景点 `first_stop`（`_first_stop_from_itinerary`，
  正则取「第一天」段首个带景点后缀的名字 + 清洗时间/动词前缀）。
- `_modify` 把**原行程**喂给 LLM，要求「只改提到的天，其余原样保留」，输出完整修改后行程并更新缓存。
- `latency_budget_ms: 3000 → 20000`：多日行程是 LLM 重生成（慢系统），3s 必超时。

### 3. 确认收尾 = 第一站导航 + POI 选择（_finalize）

`_plan`/`_modify` 的确认轮（`meta.confirmed=="true"`）→ `_finalize`，**绝不再返回 NEED_CONFIRM**（防死循环）：

- 把行程第一天景点（如天坛公园）作目的地，**实时搜 POI** 出 plain `poi_list`（无 purpose →
  HMI「第N个」改写成就近导航，见 `App.tsx`）。搜不到退化到规划期缓存的热门景点，再不行直接确认+导航目的地。
- 对齐 food-ordering/parking-payment 的 `confirmed` 范式：**确认轮直接收尾，不调子 Agent/LLM、不重规划**。

### 4. 确认词「占据整句」判定（engine.py `_confirm_reply`）— 根因级

旧逻辑用**子串包含 + ≤8字**做语音确认兜底，"行程"里的"**行**"正好是肯定词（"行"="ok"）→
「第二天行程换一个」被误当确认。同类：「可以换X」含"可以"、「第二天不要去长城」含"不要"。

修复：肯定/否定词必须**近似占据整句**（`len(文本) ≤ 词长 + slack`，yes=+2 / no=+3）才算确认/取消，
不再宽松子串包含。`_is_bare_confirm_word`（孤儿"确认"拦截）改为直接委托 `_confirm_reply(text, False)`，判定一致。

### 5. 孤儿确认护栏（engine.py）

挂起任务丢失（TTL 过期/上一步异常/重复点击）时，裸"确认/取消"**绝不下交 Planner**——否则它会借
对话历史把"确认"重规划成上一意图的重复执行（反复 trip.modify）。无挂起 + 裸确认 → 直接回
「当前没有待确认的操作」。

### 6. 跨 Agent 会话上下文透传（agents/_sdk）— 影响所有复合 Agent

- **meta 透传**：`AgentClient.call()` 此前给子 Agent 的 meta 只带 `call_depth/call_stack`，把定位/电量全丢了。
  现从 `BaseAgent.agents` 把父请求 meta（`_current_meta`）传入，转发除护栏键外的全部上下文
  （`current_lat/lng`、`vehicle_battery`、trace 等），`call_depth/call_stack` 由本层权威覆盖。
  否则 trip-planner 内部调 `charging.plan` 永远没定位 → 误报"请开启定位"。
- **Struct→dict 修复**：`AgentClient` 旧用 `dict(resp.ui_card.fields)`，留下 protobuf Value 对象，
  `r.ui_card.get("type")=="poi_list"` 永远为假——**复合 Agent 从来没拿到过子 Agent 的卡片数据**。
  改用与云端一致的 `MessageToDict(..., preserving_proto_field_name=True)`。

### 7. 电量一致性（charging_planner + edge）

- 边端 `server.py` 把 VAL 真实电量注入 `request.meta["vehicle_battery"]`，云端 `engine._build_context`
  prefs 白名单透传，charging-planner `_resolve_soc` 优先用它（回退 memory）。否则 provider 默认 50%，
  与可观测台/仪表（如 72%）不符。

## 易再踩约束（跨组件）

- **行程兜底必须在 `build()` 末端**，不能只在解析路径——降级路径会绕过。
- **trip.plan 与 trip.modify 兜底互斥**：先判 modify（更具体）再判 plan。
- **确认词不要做子串包含**：任何"含某肯定/否定词即判定"的写法都会被更长指令里的子串劫持。
- **复合 Agent 的子调用要透传父 meta**：新建 sub-planner 用 `self.agents.call()`，定位/电量/trace 自动继承；
  子 Agent 返回的 `ui_card` 是原生 dict（已 MessageToDict），可直接 `.get(...)`。
- **改 planning.py / engine.py 必须重建 cloud-planner**，改 trip-planner agent 必须重建 trip-planner-agent
  （无卷挂载）。

## 测试

全量 `python -m pytest --import-mode=importlib`：**783 passed, 6 skipped**（2026-06-24 实测）。本轮新增回归覆盖：
出行/修改确定性兜底（含降级路径、互斥、边界）、确认词占据整句（行/可以/不要不误判）、孤儿确认不重规划、
trip-planner 改行程保留上下文 + 确认收尾不循环 + 第一站取行程首日景点搜 POI、跨 Agent meta 透传与
Struct→dict 转换、电量 72% 透传。
