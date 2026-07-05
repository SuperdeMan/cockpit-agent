# 周边发现 Agent 重构（food-ordering → nearby）：基于高德 POI 2.0 的富数据周边搜索 + 详情增强

- **状态**：**P0 已落地并真栈验证（2026-07-05，真高德端到端）**；P1/P2 待做（见 §11）
- **交付对象**：Claude Code（后续按分阶段清单执行落地）
- **关联代码**：`agents/food_ordering/*`（现状 mock）、`agents/navigation/src/providers/amap.py`（高德 POI 2.0 样板）、`agents/_sdk/http.py`（出站 HTTP/熔断/可观测）、`hmi/src/types.ts` + `hmi/src/components/Cards.tsx`（卡片契约与渲染）、`orchestrator/cloud/route_hints.py`（确定性路由引擎）、`deploy/docker-compose.yaml`（服务/沙箱/密钥）、`deploy/envoy-proxy.yaml`（出站白名单）
- **关联文档**：`docs/guides/provider-integration.md`（接 provider 唯一标准流程）、`CLAUDE.md` §3/§4/§5（新增 Agent 流程、命名、安全红线）、`docs/conventions.md`

---

## 0. 决策纪要（已与泓舟锁定，2026-07-05）

| 决策 | 结论 |
|---|---|
| 范围 | **升级为通用「周边发现」Agent**，覆盖餐饮/酒店/景点/影院/停车/充电/加油等多类目 |
| 命名 | **重命名** `food-ordering` → `nearby`（agent_id / 目录 / intent 命名空间同步） |
| 与导航边界 | **发现归 nearby、出行归 navigation**——nearby 用 manifest `route_hints` 声明式接管「发现/详情/比较」说法（含「附近的川菜馆」）；navigation 只留出行动作与多意图子步搜索。**不改编排核心** |
| 点单/订位 | 降为 `require_confirm` **预留桩**（诚实告知未接入、给电话+导航兜底），不做假下单；真实 pre-order 适配器 P2 预留 |
| Provider 架构 | nearby **自持** 富数据 `AmapPlaceProvider`（复用 `_sdk/http`），**不动** navigation 的薄 `AmapPOIProvider`；共享抽取列为可选后续 |

> 我未选「把发现能力并进 navigation、删掉本 Agent」：navigation 是 `first_party` 带 `navigation.control`，塞进第三方风格的发现+点单会污染信任/权限边界，也让它变巨石。

---

## 1. 现状与证据

### 1.1 food-ordering 全是 mock，且卡片根本没渲染
- `agents/food_ordering/src/providers/mock.py` 造确定性假数据；工厂 `agents/food_ordering/src/providers/__init__.py:7-12` 里 `dianping` 分支是空 `TODO`，任何情况都返回 `MockRestaurantProvider`。
- Agent 产出 `restaurant_list` / `reservation` 卡（`agents/food_ordering/src/agent.py:44,75`），但 **HMI 未渲染**：`hmi/src/types.ts` 的 `UiCard` 联合类型（第 39-57 行）不含这两种；`hmi/src/components/Cards.tsx` 的 `CardRenderer`（第 100-123 行）无对应 `case`，落到 `default: return null`。→ 用户搜完只有语音、没有卡。

### 1.2 容器是第三方沙箱且未注入高德密钥
- `deploy/docker-compose.yaml:207-221`：`food-ordering-agent` 为 `read_only: true` + `HTTP_PROXY=http://http-proxy:8080` + `mem_limit: 256m` 的第三方沙箱，**没有 `AMAP_KEY` / `POI_VENDOR`**（对比 `navigation-agent:194-195` 有）。
- 出站白名单 `deploy/envoy-proxy.yaml:53` 已有 `restapi.amap.com` 直通 + `domains: ["*"]`（第 34 行）→ 沙箱内经代理可达真实高德。

### 1.3 高德 POI 2.0 已接好，只是「取得太少」
- `agents/navigation/src/providers/amap.py` 用的正是 POI 2.0：`/v5/place/around`（周边，带 `distance`）、`/v5/place/text`（关键字）、`/v5/place/detail`（详情），已带 `show_fields=business`（第 101 行），并处理了高德的两大坑：**坐标 `lng,lat`（经度在前）** 与 **HTTP 200 但 `status!="1"` 即业务失败**（第 39-42 行）。
- 但只映射了 `business.rating`（第 78 行），**丢掉了** `business.cost`（人均）/`tel`（电话）/`opentime_*`（营业时间）/`tag`（特色）/`photos`（图片）——正是本次要补的富数据。
- charging-planner（`agents/charging_planner/src/providers/amap.py:14`）、trip-planner、info 都在复用这套；`AMAP_KEY` 已在这些服务注入。

### 1.4 navigation 已有 POI 搜索，是本次的边界对象
- `navigation.search_poi`（`agents/navigation/src/agent.py:93`）已能搜餐厅/充电/停车等，但数据薄（`POI` dataclass 无人均/电话/营业时间/图片），且**面向「去哪」**（首选动作是 `navigate`）。这是重叠来源，§4 明确切分。

---

## 2. 问题陈述

1. **假数据**：点餐能力对用户无实用价值，与「接地车辆/真实世界」的产品护城河背道而驰。
2. **信息维度太窄**：只能出「名字+假评分+假人均」，无法回答「这家电话多少/几点关门/人均多少/有什么特色/看看图」。
3. **能力孤立**：餐饮之外的高频周边需求（酒店/景点/影院/停车）散落在 navigation 的薄搜索里，体验割裂。
4. **闭环断裂**：卡片不渲染、无「详情→导航/拨打」的顺畅 handoff。
5. **预订是伪能力**：`reserve` 假装下单成功（`agent.py:73` 出「已订好」），既不真实也不诚实。

---

## 3. 目标与非目标

### 目标
- G1 **真数据多类目周边搜索**：一个 `nearby.search`，参数化 `category`，覆盖餐饮/酒店/景点/影院/停车/充电/加油等，支持菜系/品牌/评分/人均/排序过滤。
- G2 **详情增强**：`nearby.detail` 出富卡——评分、人均、电话、营业时间、特色标签、图片、地址。
- G3 **卡片渲染 + 闭环 handoff**：新增 `place_list` / `place_detail` 卡并在 HMI 渲染；支持「看第 N 个详情」「导航去第 N 个」「拨打电话」。
- G4 **与 navigation 干净切分**：发现归 nearby、出行归 navigation，经 `route_hints` 声明式路由，不改编排核心。
- G5 **诚实降级**：无凭证/超时回退 mock；高德没返回的字段（人均/评分常缺）绝不编造，话术按「是否已知」自适应（对齐 charging 的做法）。

### 非目标（本次不做）
- 真实在线点单/订位下单（连锁 pre-order）——仅留桩，P2 预留适配器接口。
- 外卖、团购券核销、排队取号、预订支付扣款。
- 把 navigation 的薄 `AmapPOIProvider` 抽到 `_sdk` 共享（可选后续，见 §7 P2）。
- 高德实时字段（如充电桩空闲枪数）——基础 POI 不返回，不编造。

---

## 4. 架构决策

### 4.1 边界：发现（nearby） vs 出行（navigation）

| 维度 | nearby（新） | navigation（不动核心逻辑） |
|---|---|---|
| 用户意图 | 「附近有什么好吃的」「这家怎么样」「附近的酒店/电影院/景点」「人均多少」「找家评分高的火锅」 | 「导航去 X」「带我去」「回家」「顺路/途经 Y」「我在哪」 |
| 价值 | 帮用户**决定去哪**（富信息、比较、详情） | **把人送过去**（路线、途经点、常用地点） |
| 首选动作 | 出富卡；follow-up「导航去第 N 个 / 看详情 / 拨打」 | `navigate` 动作 |
| 数据 | 富 `Place`（人均/电话/营业时间/特色/图片） | 薄 `POI`（名/址/评分/距离） |
| 信任级 | `third_party`（读外部 API，不碰车控） | `first_party`（`navigation.control`） |

**重叠处理**：「附近的川菜馆」这类无动词的发现说法，由 nearby 经 `route_hints` 接管（§4.5）；navigation 在**多意图子步**里（如「导航去东方之门，顺路找家吃的」）仍自己搜餐厅做途经点候选——那是 navigation 内部流程，不经 `route_hints`，互不影响。

### 4.2 Intent 与 slot 契约

```yaml
capabilities:
  - intent: nearby.search           # 周边搜索（类目参数化）
    slots: [category, keyword, cuisine, brand, rating_min, price_max, price_level, open_now, sort, location, radius]
    heavy: false
    ui_card: { display_priority: 1 } # 多意图下作交互候选卡（可选主卡）
  - intent: nearby.detail           # 详情增强
    slots: [poi_id, name]
  - intent: nearby.order            # 点单/订位预留桩（require_confirm）
    slots: [poi_id, name, datetime, party_size]
    require_confirm: true
```

**slot 语义**：
- `category`：类目枚举（餐饮/美食、酒店、景点、影院、停车场、充电站、加油站、超市、咖啡…）。缺省=餐饮。
- `keyword`：自由关键词（当 category 无法表达时的兜底，如具体店名）。
- `cuisine`：菜系（川菜/火锅/日料…），仅餐饮细化。
- `brand`：品牌（麦当劳/瑞幸/星巴克…），高德按品牌返回连锁门店。
- `rating_min` / `price_max` / `price_level`：评分下限 / 人均上限 / 价位档（客户端过滤，见 §4.3）。
- `open_now`：仅要营业中（P1，需解析 `opentime_today`）。
- `sort`：`distance`（默认）/ `rating`。
- `location` / `radius`：区域名或坐标 / 半径（缺省用本轮已授权 GPS）。

**category → 高德检索词映射**（`_category_keyword(category, cuisine, brand, keyword)`）：关键词优先、稳健；`types` 编码为可选精确化（以高德《POI 分类编码表》为准，实现时核对）。

| category | 主关键词 | 可选 types |
|---|---|---|
| 餐饮/美食 | `cuisine` 或「美食」 | 050000 |
| 酒店/住宿 | 「酒店」 | 100000 |
| 景点/景区 | 「景点」 | 110000 |
| 影院/电影院 | 「电影院」 | 080601 |
| 停车场 | 「停车场」 | 150900 |
| 充电站/充电桩 | 「充电站」 | 011100 |
| 加油站 | 「加油站」 | 010100 |
| 其他（超市/咖啡/药店/银行/医院…） | category 原词 | — |

> `brand` 非空时直接作关键词（连锁精确匹配）；`cuisine` 非空时覆盖「美食」。

### 4.3 Provider 层设计（`agents/nearby/src/providers/`）

遵循 `docs/guides/provider-integration.md` 三层范式，nearby **自持** provider，不改 navigation。

**`base.py` —— 领域接口 + 富 dataclass**：
```python
@dataclass
class Place:
    id: str = ""
    name: str = ""
    category: str = ""        # 高德 type 主类目
    address: str = ""
    lat: float = 0.0
    lng: float = 0.0
    distance_km: float = 0.0
    rating: float = 0.0       # business.rating（常缺→0，不编造）
    cost: str = ""            # business.cost 人均（字符串，可能空）
    tel: str = ""             # business.tel 电话（可能多号 ; 分隔）
    open_today: str = ""      # business.opentime_today
    open_week: str = ""       # business.opentime_week
    tags: str = ""            # business.tag 特色（逗号分隔）
    area: str = ""            # business.business_area 商圈
    photos: list[str] = field(default_factory=list)  # photos[].url

class PlaceProvider(ABC):
    async def search(self, keyword, *, category="", near=None, rating_min=0,
                     price_max=0, brand="", open_now=False, sort="",
                     limit=10, page=1, meta=None) -> list[Place]: ...
    async def detail(self, place_id="", *, name="", near=None, meta=None) -> Place: ...
```

**`amap.py` —— `AmapPlaceProvider`（富 `show_fields`）**：
- HTTP 一律走 `AsyncHttpClient(vendor="amap", service="nearby")`；复用 navigation 已验证的坐标坑/`status!="1"` 判定/geocode 逻辑（可照抄 `agents/navigation/src/providers/amap.py:36-66`）。
- 请求 `show_fields=business,photos,children`（比 navigation 多 `photos`）。
- **字段映射表**（POI 2.0，实现时对照高德官方文档核对字段名）：

  | 领域方法 | 厂商 endpoint | 厂商字段 → 领域字段 | 坑 |
  |---|---|---|---|
  | `search`（有定位） | `/v5/place/around` | `pois[].name→name`、`location("lng,lat")→lng,lat`、`distance→distance_km(/1000)`、`business.rating/cost/tel/opentime_today/opentime_week/tag/business_area→…`、`photos[].url→photos` | 坐标 **lng,lat**；`status!="1"` 即失败；空字段常返回 `[]`（照 `_as_str` 归一） |
  | `search`（无定位） | `/v5/place/text` | 同上，无 `distance` | 需带 `region`/城市时另议；PoC 无定位可诚实提示 |
  | `detail` | `/v5/place/detail` | `id→id` 取详情，同 business/photos 映射 | 有 `poi_id` 才走；否则先 `search(name, limit=1)` 取首个再 detail |

- **过滤/排序**（高德 v5 过滤能力有限，统一客户端处理）：`rating_min`→`p.rating>=rating_min`；`price_max`→解析 `cost` 为数值后 `<=price_max`；`open_now`→解析 `open_today`（P1）；`sort=rating`→按 `rating` 降序（`distance` 默认按高德返回序）。
- 失败抛 `ProviderError`，Agent 侧降级 mock（§4.4）。

**`mock.py`**：确定性富假数据（含人均/电话/营业时间/特色），供离线/单测/降级。

**`__init__.py` 工厂**（与 navigation 同门控，复用 `POI_VENDOR`/`AMAP_KEY`）：
```python
def build_place_provider() -> PlaceProvider:
    if os.getenv("POI_VENDOR") == "amap" and os.getenv("AMAP_KEY"):
        try:
            from .amap import AmapPlaceProvider
            return AmapPlaceProvider(os.getenv("AMAP_KEY"))
        except Exception as e:
            logger.warning("AmapPlaceProvider init failed, fallback mock: %s", e)
    return MockPlaceProvider()
```

### 4.4 Agent 业务逻辑（`agents/nearby/src/agent.py`）

沿用 `BaseAgent`；`self.place = build_place_provider()` + `self._fallback = MockPlaceProvider()`。

- **`nearby.search`**：
  1. 组装 `keyword=_build_keyword(category, cuisine, brand, keyword)`；定位取 `current_location_from_meta(meta)`（与 navigation/天气一致，只用本轮已授权 GPS；无定位→关键字检索或诚实提示，不拿任意城市冒充「附近」）。
  2. `results = place.search(...)`，`ProviderError` → `self._fallback`（失败已由 provider span 记录，不静默）。
  3. 客户端过滤/排序（§4.3）。
  4. **口味画像**：保留现有 `ctx.recall("口味偏好", predicate_prefix="taste.")` 逻辑（`agent.py:48-57`），仅餐饮类目时并入话术。
  5. 话术**按是否已知自适应**：有人均说人均、有评分说评分，缺则不提、不编造。
  6. 出 `place_list` 卡（带 `data.items` 供编排 `slot_refs` 取值 + `display_priority`）；follow-up：「说『看第 2 个详情』或『导航去第 1 个』」。
- **`nearby.detail`**：有 `poi_id`→`place.detail(id)`；无 id 有 `name`→`place.search(name, limit=1)` 取首个。出 `place_detail` 卡 + 动作 `navigate`（导航去）+ `call`（拨打电话）。
- **`nearby.order`（桩，诚实）**：`NEED_CONFIRM`；确认后**不假装下单**，返回诚实话术「在线点单/订位正在接入中（当前仅麦当劳/瑞幸等少数连锁支持），已为你保留商家电话与导航」+ `place_detail` 卡 + `call`/`navigate` 动作。P2 接真实 pre-order 适配器时替换此分支。

### 4.5 路由：manifest `route_hints`（不改编排核心）

由 `RouteHintEngine`（`orchestrator/cloud/route_hints.py`）通用消费。发现说法 `replace` 到 `nearby.search`；`guard` 让出行动词归 navigation。

```yaml
route_hints:
  # 发现/推荐说法 → nearby.search；guard 命中导航动词则不生效（让给 navigation）
  - pattern: '附近|周边|就近|旁边|这边有(没有|啥|什么)|哪(有|家|里有).{0,6}(好吃|好玩|餐厅|饭店|美食|川菜|火锅|日料|咖啡|奶茶|酒店|宾馆|景点|景区|电影院|影院|停车场|加油站|超市)|(推荐|找|来)(个|家|点).{0,4}(餐厅|吃的|美食|馆子|酒店|景点|电影)'
    intent: nearby.search
    policy: replace
    priority: 60
    guard: '导航|带我去|去哪|回家|回公司|路线|怎么走|途经|顺路'
    slots: { keyword: "$text" }
  # 「这家/那家怎么样、评分/人均/电话/几点关门」→ nearby.detail（承接上一轮卡片选择）
  - pattern: '(这家|那家|这个|它)(怎么样|好不好|评分|人均|多少钱|电话|几点(关|开)|营业)|看(看|下)(它的)?详情|详细信息'
    intent: nearby.detail
    policy: replace
    priority: 60
    slots: { name: "$text" }
```

> 具体 pattern 需经 `test/eval_route_hints.py --dump` 对真实 manifest 实测微调（对齐 R3.4 做法），避免与 navigation/trip/deep-research 的 hint 互扰。**「导航去第 N 个」的 ordinal→navigate 由 HMI/编排既有链路处理，不在此 hint 内。**

### 4.6 信任 / 权限 / 沙箱

- `trust_level: third_party` 保留（读外部 API，不碰车控，合理）。
- `requires_permissions`：`location.read` + `network.external`（搜索必需）；`payment.invoke` 保留但**仅 `nearby.order` 桩用**，注释标注。
- `context_scopes: [location]`（周边搜索要精确定位；不需 `vehicle_state`）。
- 沙箱保留（`read_only` + `http-proxy` + `mem_limit`）；**注入 `AMAP_KEY` + `POI_VENDOR`**。provider 用 `_sdk/http`（httpx，不写盘），与 `read_only` 兼容。

---

## 5. HMI 卡片契约与渲染

### 5.1 `types.ts` 新增两种卡（加入 `UiCard` 联合）
```ts
export type PlaceListCard = {
  type: 'place_list'
  category?: string        // 餐饮/酒店/景点…（卡头与文案用）
  keyword?: string
  items: Array<{
    id: string; name: string; category?: string
    rating?: number; cost?: string; distance_km?: number
    address: string; tags?: string; open_today?: string
  }>
}
export type PlaceDetailCard = {
  type: 'place_detail'
  id: string; name: string; category?: string; address: string
  lat: number; lng: number
  rating?: number; cost?: string; tel?: string
  open_today?: string; open_week?: string; tags?: string
  photos?: string[]
}
```

### 5.2 `Cards.tsx` 渲染
- `CardRenderer` 加 `case 'place_list'` / `case 'place_detail'`。
- `place_list`：复用 `PoiListCardView` 视觉骨架（`Cards.tsx:1065`），每项多显示**类目芯片 / 人均 / 营业中**；底部 follow-up 提示复用「说『导航去第 N 个』」+ 新增「『看第 N 个详情』」。**卡片必须携带 `data.items`（同 poi_list），令『第 N 个』的 ordinal handoff 走既有链路**（实现时验证）。
- `place_detail`：比 `PoiDetailCardView`（`Cards.tsx:1104`）更富——地址/评分/人均/营业时间/特色标签，加**「导航」「拨打电话」两个动作按钮**；`photos` 存在则显示缩略图（外链图片，见 §9 风险）。

### 5.3 `AGENT_CATALOG`（`types.ts:389`）
- 条目 `food-ordering` → `nearby`：`label:'周边发现'`、`desc:'找餐厅/酒店/景点/影院/停车/充电，看评分·人均·营业·电话'`、`icon` 换发现类（如 `📍`）。
- **兼容**：`DEFAULT_SETTINGS.agents` 由 catalog 派生，重命名后默认含 `nearby:true`；用户已持久化的旧 `food-ordering` key 无害（合并时多余键忽略）——实现时确认设置合并逻辑不因缺 `nearby` key 报错。

---

## 6. 安全红线对齐（`CLAUDE.md` §5）

- ✅ 本 Agent 不碰车控（发现/详情只读）；`navigate` 动作仍经既有 navigate 链路 → VAL。
- ✅ LLM 不直连车控；provider 只做检索解析。
- ✅ 凭证 `AMAP_KEY` 经 env，不进代码/日志；compose 只注入本服务（最小化）。
- ✅ 敏感数据最小化：`context_scopes:[location]` 按需下发精确定位。
- ✅ 点单桩不真实扣款；真实 pre-order（P2）走统一 `payment-gateway`，Agent 不持支付凭证。

---

## 7. 分阶段落地

### P0 — 真数据可用（餐饮 + 多类目搜索/详情 + 卡片渲染 + 重命名）
1. **重命名与迁移**（§8 清单）：`git mv agents/food_ordering agents/nearby`，改包名/导入/Dockerfile/manifest/compose。
2. **Provider**：`base.py`（`PlaceProvider`+`Place`）、`amap.py`（`AmapPlaceProvider`，富 `show_fields`，字段映射）、`mock.py`（富假数据）、`__init__.py` 工厂。
3. **Agent**：`nearby.search`（多类目 + 定位 + 评分/人均/品牌过滤 + 口味画像 + 话术自适应 + `place_list` 卡）、`nearby.detail`（`place_detail` 卡 + navigate/call）、`nearby.order`（诚实桩）。
4. **manifest**：intents、`route_hints`、permissions、`context_scopes`、`ui_card.display_priority`。
5. **HMI**：`types.ts` 两卡 + `AGENT_CATALOG`；`Cards.tsx` 两个 View + 动作按钮。
6. **compose/env**：`nearby-agent` 注入 `AMAP_KEY`+`POI_VENDOR`，保留沙箱/proxy；确认 `envoy-proxy` 直通 `restapi.amap.com`。
7. **单测**：provider 黄金响应（字段映射/坐标坑/`status!=1`/过滤/降级）+ agent 分发/话术/卡片/桩；改写现有 `test_agent.py`。
8. **`make test` 全绿**。

**P0 验收**：真高德下「附近有什么好吃的」「附近评分高的火锅」「附近的酒店/电影院/停车场」出真实 `place_list`；「看第 1 个详情」出含电话/营业时间/人均的 `place_detail`；无 `AMAP_KEY` 优雅回退 mock。

### P1 — 体验精修
- 「营业中」`open_now`（解析 `opentime_today`）、`sort=rating`、价位档 `price_level`。
- 品牌/菜系过滤打磨；`place_detail` 图片渲染（外链 + CSP 验证 + 降级）。
- 「导航去第 N 个 / 看第 N 个详情」handoff 全链路打通并测。
- 口味画像接地（餐饮时排序偏好）。
- `route_hints` 经 `eval_route_hints.py` 与 navigation/trip/research 交叉验证，消歧。

### P2 — 点单预留 + 收敛
- 真实 pre-order 适配器接口（麦当劳/瑞幸风格）预留实现，替换 `nearby.order` 桩；走 `payment-gateway`。
- 多类目卡片字段差异细化（酒店价格区间/景点门票、影院场次留待有 provider）。
- **可选**：把高德 `_get`/geocode/坐标解析抽到 `_sdk/amap`，navigation 薄 provider + nearby 富 provider + charging 共享一个客户端（消除 charging 的跨 Agent import）。

---

## 8. 迁移与兼容（重命名 ripple 清单）

> 落地前先全仓 `grep -rn 'food.search_restaurant\|food.reserve\|food-ordering\|food_ordering\|FoodOrdering'` 逐一迁移。

- [ ] `git mv agents/food_ordering agents/nearby`；包内 `food_ordering`→`nearby`（`main.py`/`agent.py`/`__init__.py`/tests 的导入）。
- [ ] `manifest.yaml`：`agent_id: food-ordering`→`nearby`、`display_name: 周边发现`、capabilities/route_hints/permissions/context_scopes/ui_card。
- [ ] `Dockerfile`：`dockerfile: agents/nearby/Dockerfile`；`COPY`/入口路径。
- [ ] `deploy/docker-compose.yaml`：服务 `food-ordering-agent`→`nearby-agent`、build 路径、`AGENT_PORT` 沿用 `50063`、注入 `AMAP_KEY`+`POI_VENDOR`、保留沙箱/proxy/depends_on。
- [ ] intent `food.search_restaurant`/`food.reserve` → `nearby.search`/`nearby.order`（+ 新增 `nearby.detail`）。
- [ ] HMI：`AGENT_CATALOG` id/label/icon/desc；`demo.ts` 若有 food 卡样例同步。
- [ ] 路由语料/评测：`test/eval_corpus/*`、`orchestrator/cloud/tests/test_route_hints.py`、`route_hints_cases.yaml` 里的 food.* 引用。
- [ ] 文档：`AGENTS.md`、`README.md` 的 food-ordering 描述；本 Agent `README.md` 重写。
- [ ] **无需保留 `food.*` 别名**：云端 Planner 从注册能力集（各 manifest 的 intents/examples）生成 intent，重命名后不会再产出 `food.*`；`_validated_steps` 也会丢弃非法 intent。（如担心历史缓存，可临时留 alias，P2 删。）
- [ ] navigation `agent.py:328` 关于「food.search_restaurant 恒 mock」的注释更新（现已是 nearby 真数据，但 navigation 子步逻辑不变、不依赖它）。

---

## 9. 风险与未决

| 风险 | 处理 |
|---|---|
| 高德字段名/`show_fields` 细节与记忆不符 | 实现 §4.3 前**对照高德官方 POI 2.0 文档核对**，先列字段映射表再写代码（provider 指南 Step 2 纪律）。 |
| 评分/人均覆盖率低（很多 POI 无 business 字段） | 话术**按是否已知自适应**、不编造（对齐 charging）；卡片缺字段不显示，不占位假数据。 |
| `place_detail` 外链图片被 HMI CSP 拦 | P1 验证高德图床域名；被拦则详情卡不显示图片、不阻断主体。 |
| route_hints 与 navigation/trip/research 互扰 | 经 `eval_route_hints.py --dump` 实测；`guard` 让出行动词归 navigation；priority 取 60（低于 research=100/trip=90，避免抢重域）。 |
| 「第 N 个」ordinal handoff 是否复用既有链路 | P0 令 `place_list` 携带 `data.items`（同 poi_list）；P1 端到端验证「导航去第 N 个 / 看第 N 个详情」。 |
| 沙箱 `read_only` 与 provider 写盘冲突 | provider 只用 `_sdk/http`（httpx，内存），不写盘；确认无本地缓存落盘。 |
| 充电类目与 charging-planner 职责重叠 | nearby 只做「充电站发现罗列」；「按 SoC 沿途补电规划」仍归 charging-planner（`route_hints` 不抢「充电规划/沿途/续航」说法）。 |

---

## 10. 验收标准（真栈端到端，对齐项目惯例）

1. `make test` 全绿（新增 provider 单测 + agent 契约测试）。
2. `test/e2e_real_providers.py` 加 nearby 一条真冒烟（无 `AMAP_KEY` 自动 skip，断言识破静默回退 mock）。
3. 起全栈（`nearby-agent` 注入 `AMAP_KEY`），CDP 驱动 headless 打真后端：`附近有什么好吃的`→`place_list` 真数据；`看第 1 个详情`→`place_detail` 含真实电话/营业时间；`附近的酒店/电影院`→多类目真数据；`导航去第 2 个`→handoff 到 navigate。
4. 路由：`附近的川菜馆`归 nearby、`导航去 X`归 navigation（`eval_route_hints.py` 用例覆盖）。
5. 降级：停 `AMAP_KEY` / 断网 → 优雅回退 mock，不 500。

---

## 11. P0 落地记录（2026-07-05，分支 `feat/nearby-discovery-redesign`）

**已落地**：重命名 `food_ordering→nearby`（git mv 保留历史）+ 富数据 `AmapPlaceProvider`（`show_fields=business,photos`）+ `PlaceProvider`/`Place` + mock + 工厂；`nearby.search`/`nearby.detail`/`nearby.order` 三 intent（`_clean_name` 按名剥壳解析、诚实 order 桩）；manifest `route_hints`（发现说法接管、`guard` 让出行归 navigation）；HMI `place_list`/`place_detail` 卡 + `AGENT_CATALOG` + 「第N个」/「看第N个详情」handoff + 详情导航/拨打按钮；`food.*→nearby.*` 全仓迁移（编排 few-shot / `agent_client` 端口表 / eval 语料 / 单测）；registry resolve 基线重算 15/15（其间发现并修复「订一家川菜馆」关键词层回归——补回代表性食例）。

**验证**：全量 `pytest` **1087 passed / 7 skipped**（零回归）；HMI `vite build` 通过（`tsc --noEmit` 仅余既有 `.mjs` 无声明噪声，无本次新错）；nearby 单测 20 例（agent + provider 黄金响应）。

**真栈端到端（真高德，25 容器，`ws://…:8090/ws`）**：
- 「附近有什么好吃的」→ `place_list`，真实天安门周边 POI（端门快餐 / 老北京炸酱面 / 程府宴…），rating/人均真数据；
- 「附近的酒店」→ 人民大会堂宾馆(4.8)…（酒店无人均 → 话术**不编造**，正确留空）；
- 「附近评分高的火锅」→ 温鼎府·海鲜火锅(4.8 / 人均449)…；
- 「端门快餐怎么样」→ `place_detail`：评分3.7 / 人均47 / 营业08:00-16:30 / 地址故宫内 / 3 张图（无电话 → **不编造**）。

**⚠ 真栈发现——第三方出站代理是空壳（P0 已绕过，待独立硬化）**：nearby 初配 `HTTP_PROXY=http-proxy:8080`（ws8 沙箱）时真调用全 `All connection attempts failed` → 降级 mock。根因=`deploy/envoy-proxy.yaml` 的 http-proxy **不是可用正向代理**：它是 `http_connection_manager` 把所有 `/` round-robin 混发到 amap/serpapi/qweather 等 5 个上游、且不支持 HTTPS 的 `CONNECT` 隧道——旧 food-ordering/parking 全 mock，**这条沙箱→代理→外网路径从未被真调用压过**，是 ws8 遗留空壳。**P0 处置**=nearby **直连高德**（与 navigation/info/charging/trip-planner 完全一致，均无代理；nearby 代码仅硬编码调 `restapi.amap.com`），保留其余沙箱（`read_only`/`mem_limit`/`no-new-privileges`）。**建议独立小卡**：若要恢复第三方 Agent 出站白名单，需把 envoy 改成真正的 forward-proxy（`dynamic_forward_proxy` + CONNECT + 按域 allowlist）或换支持 CONNECT 的轻量代理——这是 ws8 既有缺口，非本次重构引入。

**P0 未覆盖（转 P1）**：HMI 浏览器像素级渲染 + 「第N个」handoff 的 CDP 实测（数据契约已验、组件已编译通过）；`open_now` 营业中过滤 / `sort=rating` 精修；详情卡图片 CSP 验证。
