# 赛事多轮：某场进球详情（2026-06-23）

> 让 `info.sports` 支持「第N场/某队 + 谁进的球/详细赛况」的多轮追问。实现见 `agents/info/src/agent.py`、`agents/info/src/providers/sports_apifootball.py`、`agents/info/src/providers/base.py`、HMI `hmi/src/components/Cards.tsx`。延续 `2026-06-22-search-quality-and-card-redesign.md` 的赛事结构化真相源原则。

## 1. 背景与问题

实测多轮：
- 轮1「查世界杯赛况」→ 正确返回当天 4 场列表 ✅
- 轮2「第一场比赛的详细赛况，是谁进的球？」→ **被无视**，把同一张列表又返回一遍 ❌

根因：`info.sports` 只有「按日期+联赛列全部」一条路（`_do_sports`），既不解析「第一场/某队」指代到具体某场，也没有进球射手数据——provider 只有 `fixtures()`，连 `fixture.id` 都没存。

## 2. 数据层（provider）

- `SportsFixture` 补 `fixture_id / home_id / away_id`（`fixtures()` 解析时一并捕获）。
- 新增 `GoalEvent` + `events(fixture_id)`：`GET /fixtures/events?fixture={id}`。
- **只取真实进球**：`type=="Goal"` 且 `detail ∈ {Normal Goal→进球, Penalty→点球, Own Goal→乌龙球}`。
  - **关键坑**：api-football 把 **`Missed Penalty`（罚丢点球）也标 `type=Goal`**，必须按 detail 过滤，否则谎报射手/进球（实测阿根廷那场第 9 分钟 Messi 罚丢点球 + 第 38 分钟进球，只能算后者）。
- `minute` 含补时（`elapsed`+`time.extra`，如 `45+2`）。

## 3. 编排层（agent）

- **定位某场** `_pick_fixture(text, fixtures)`：序号（`第N场/首场/最后一场`，按列表顺序＝用户在卡上看到的顺序）优先，其次队名（中文）命中；都没有 → `None`（维持列表）。
- **列表守卫** `_is_list_request`（全部/有哪些/还有…）：带队名但属"列全部"诉求时不误入单场详情。
- **多轮联赛回填** `_league_from_history(ctx)`：追问句槽位常不带联赛名 → 读 `ctx.history()`（`_sdk/base.py:Context.history` 已有）从最近几轮找联赛关键词。`_sports` 在 `_detect_league` 落空且命中追问特征（detail/序号/赛事词）时触发。
- **进球详情** `_match_detail(fixture, league, meta)`：调 `events()`，按 **team_id 比 home_id/away_id** 定主客（跨语言可靠，不靠队名）。
  - 语音：`{联赛}，{主} {比分} {客}（{状态}）。进球：第X分钟{射手}（{球队}{点球/乌龙球标注}）…`；0-0 进行中→「目前还没有进球」；有比分但取不到事件→「暂未获取到进球详情」；未开赛→「比赛尚未开始」。**不编造**。
  - 卡片：复用 `sports_scores`，`fixtures=[该场]`，fixture 加 `goals:[{minute,team,player,detail}]`。
- 接线：`_do_sports` 拉到 fixtures 后 `picked = _pick_fixture(...)`，`picked` 且非列表诉求 → `_match_detail`，否则维持分组列表。

## 4. 展示层（HMI）

`SportsFixture` 类型加可选 `goals?`；`FixtureRow` 在 `f.goals?.length` 时行下渲染进球时间线（⚽ 分钟 + 射手，主队左对齐、客队右对齐镜像，点球/乌龙球小标注）。普通列表行不受影响，无新增卡片类型。

## 5. 取舍

- **无状态**：「第N场」靠重取当天列表 + 序号，不存会话态——更稳、与卡片顺序天然一致。
- **聚焦进球**：详细赛况只给射手+分钟（座舱 TTS 最关心「谁进的」）；黄牌/换人/技术统计不在本次。
- **球员名用 api 原名**（多英文如 L. Messi）：真实优先，不静态翻译（球员库无穷、易错）。

## 6. 验证

`agents/info/tests`（55 passed，含 events 过滤 Missed Penalty、序号/队名定位、history 回填、列表守卫、0-0 诚实），全量 `pytest` 711 passed / 6 skipped，HMI 22 + build 通过。

**真机**（容器内真实 api-football）：轮2「第一场是谁进的球」→ 语音「FIFA 世界杯，阿根廷 1-0 奥地利（下半场89′）。进球：第38分钟L. Messi（阿根廷）。」卡片只剩该场 + 进球时间线；第 9 分钟罚丢点球被正确剔除。
