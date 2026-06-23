# 设计：常用地点（家 / 公司）+ 未设置二次交互

> 状态：已对齐，落地中（2026-06-23）。源起：C01-C20 评测 C04 发现「导航去公司」无法解析具体地点。
> 真实座舱导航普遍支持常用地点（家/公司）。本设计补齐：存储 + 别名解析 + 未设置时二次交互设置。

## 1. 问题

`navigation.navigate_to(destination="公司")` 直接把"公司"丢给高德 POI 搜索 → 搜不到具体点或返回邻近无关 POI → 返回泛化 NEED_SLOT「暂时无法确定公司」。用户无法把"公司""家"绑定到真实地址，每次都要报全名。

## 2. 目标

- 用户可设置常用地点：家(home)、公司(company)、学校(school)。
- "导航去公司/回家"命中别名 → 用已存地址直接导航。
- **未设置时二次交互**：反问地址 → 用户答 → 地理编码 → 存为该常用地点 → 直接导航过去（设置即出发，不额外确认）。
- 显式设置："把家设成XX""我家在XX" → 存并回显，不导航。
- 持久化（Redis），memory 服务重启不丢。

## 3. 架构决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 存哪里 | **memory 服务用户画像**，scope `profile.places` | memory 是画像唯一真相源（架构 §2）；别处存会割裂画像 |
| 持久化 | Redis `profile:{user_id}` | 重启不丢；与会话/挂起态同基础设施 |
| 写入通路 | 新增 `Memory.UpsertProfile` RPC | 现有 Memory 只有 Get/AppendTurn/GetSession，缺"写画像"一跳 |
| 别名解析在哪 | navigation agent（POI 搜索之前拦截） | "导航去公司""把家设成X"都是导航交互；存储归 memory，解析归 navigation |
| 设置确认 | 直接存+导航，话术回显存了哪个地址 | 用户选择；设置即出发最顺 |

## 4. 数据模型

`profile.places`（JSON）：
```json
{
  "home":    {"name": "...", "address": "...", "lat": 22.53, "lng": 114.05},
  "company": {"name": "...", "address": "...", "lat": 22.54, "lng": 113.95}
}
```
Redis key `profile:{user_id}`（整份画像 JSON，places 为其中一个字段）。PoC 单用户 `user_id="u1"`（网关注入）。

## 5. 接口

### 5.1 proto（memory.proto 新增）
```proto
rpc UpsertProfile (UpsertProfileRequest) returns (UpsertProfileResponse);

message UpsertProfileRequest {
  string user_id = 1;
  string key = 2;        // e.g. "places"
  string value_json = 3; // 该 key 的完整 JSON 值（places map 全量）
}
message UpsertProfileResponse { bool ok = 1; }
```
读仍走现有 `GetContext(scopes=["profile.places"])`。

### 5.2 _sdk
- `MemoryClient.upsert_profile(user_id, key, value_json)` → 调 UpsertProfile。
- `Context.save_profile(key, value)`（value 为 dict，内部 json.dumps）→ 便于 agent 写画像。
- `Context.fetch("profile.places")` 已可读。

### 5.3 navigation intent
- 复用 `navigation.navigate_to`：新增隐式槽 `place_address`（仅二次交互续接时出现）。
- 新增 `navigation.set_place`：slots `place`（家/公司/…）、`address`。manifest examples：「把家设成XX」「我家在XX」「设置公司地址为XX」。

## 6. 关键流程

### 6.1 已设置：导航去公司
navigate_to(dest="公司") → 命中别名 company → `ctx.fetch("profile.places")` 有 company → 直接出 navigate action（用存的 lat/lng/name）。

### 6.2 未设置：二次交互设置即出发（核心）
1. navigate_to(dest="公司")，places 无 company →
   `NEED_SLOT(missing_slots=["place_address"], speech="您还没设置公司位置，请告诉我公司地址")`。
   **关键**：用独立槽名 `place_address`，不动 destination="公司" —— 续接时别名上下文不丢。
2. 引擎挂起 wait_slot，保存计划（step.slots 含 destination="公司"）。
3. 用户答"深圳南山腾讯滨海大厦" → 引擎把原文填进 `place_address`（engine.py 现有 wait_slot 续接逻辑），重新调 navigate_to。
4. navigate_to 见 `destination=别名 + place_address 非空` → 地理编码 place_address → 取最优点 → `ctx.save_profile("places", {...company: 该点})` → 出 navigate action，话术"已把公司设为XX并为您导航"。

### 6.3 显式设置（不导航）
"我家在深圳XX" → planner → `navigation.set_place(place=家, address=深圳XX)` → 地理编码 → save_profile → "已把家设为XX"。

## 7. 别名表
`家/我家/回家→home`，`公司/单位→company`，`学校→school`。大小写/前后缀宽松匹配；非别名走原有目的地解析，零回归。

## 8. 权限
agent 内经 memory client 读写画像，不作为 step 级 gated 权限（PoC，与现有 ctx.fetch 一致）。生产再按 `profile.read/profile.write` 细化。

## 9. 影响面与重建
- 改 `proto/` → `buf generate` → memory/navigation/cloud-planner 重建（均 import memory_pb2）。
- navigation：alias 解析、set 流程、set_place handler、manifest。
- memory：UpsertProfile + places Redis 持久 + GetContext 从 Redis 读 places。
- 零回归：非别名目的地、既有 navigate/waypoint/charging 链路不变。

## 10. 测试
- memory：UpsertProfile 写入 + GetContext 读回（Redis & 内存兜底）。
- navigation：别名命中已设→直接导航；未设→NEED_SLOT(place_address)；续接 place_address→存+导航；set_place→存不导航；非别名零回归。
- 端到端（重建后人工）：设家→导航回家；导航去未设公司→给地址→存+导航→再次导航去公司直达。
