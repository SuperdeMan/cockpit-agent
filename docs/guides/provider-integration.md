# Provider 接入指南 —— 把某个 Agent 的 mock 能力换成真实厂商 API

- **类型**：常青指南（evergreen guide）。这是「接真实 provider」的唯一标准流程，照此做不跑偏。
- **适用对象**：任何要给某 Agent 接真实外部能力（地图/天气/搜索/股票/票务…）的开发者或 Agent。
- **关联代码**：`agents/_sdk/http.py`、`agents/navigation/src/providers/*`（高德样板）、`agents/info/src/providers/*`（和风/搜索/新闻/股票样板）、`observability/events.py`
- **关联文档**：`docs/architecture/detailed/ws6-real-capabilities-and-agent-collaboration.md`（范式来源）、`docs/conventions.md` **§9.3/§9.4（`_prov` 卡片真实性标记 + Provider 决议契约，2026-07-17 治理主题）**、`docs/design/2026-07-17-data-authenticity-governance.md`、`CLAUDE.md` §5（安全红线）

> **黄金法则**：Agent 业务逻辑**只依赖领域 Provider 接口**，永不直接 import 某厂商 SDK/写 `requests`。
> 真实厂商实现是「可插拔适配器」，mock/real 经 env 切换。
>
> **真实性三条铁律（2026-07-17 治理 P0-P2，取代旧「失败自动回退 mock」教义）**：
> 1. **默认 env（无凭证）→ mock**：CI/离线开发照旧可跑；
> 2. **显式 real 意图（vendor env 显式非 mock，或配了该域凭证）+ 构造失败 → fail-fast 启动即炸**，
>    绝不静默回退 mock（`agents/_sdk/provenance.py::fail`）；
> 3. **运行期真实源失败 → 诚实降级说拿不到**（FAILED 话术），**绝不改供 mock 假数据**
>    （假餐厅可能被导航过去——news/nearby 的运行期回退已因此拆除）。

---

## 1. 为什么是这个范式

直接在 Agent 里写死某家厂商 → 难替换、难测试、难灰度、外部抖动击穿主链。
所以分三层（已在 navigation/info 落地，照抄即可）：

```
agents/<name>/src/
├─ agent.py                # 业务逻辑：只调领域 Provider 接口，不认厂商
└─ providers/
   ├─ base.py              # 领域接口 + 领域 dataclass（如 POIProvider/WeatherProvider）
   ├─ mock.py              # Mock 实现（PoC/离线/单测，确定性假数据）
   ├─ <vendor>.py          # 真实厂商适配（如 amap.py / qweather.py），凭证经 env
   └─ __init__.py          # build_<x>_provider()：按 env 选 real/mock；显式 real 意图下构造失败 fail-fast
```

**收益**：换厂商业务零改动；无 key 时 PoC 仍可跑；外部失败被 Provider 层兜住；
决议结果（real/mock + 来源）全栈可审计、卡片可标记。

---

## 2. 七步接入流程（从厂商 API 文档 → 可上线代码）

### Step 1 — 定义或复用领域 Provider 接口（`base.py`）
一个「领域」一个接口，方法是**业务语义**（不是厂商 endpoint）。所有方法带可选 `meta: dict | None = None`（透传 trace）。
> 样板：`agents/navigation/src/providers/base.py`（`POIProvider.search/get_route` + `POI`/`GeoPoint`）、
> `agents/info/src/providers/base.py`（`WeatherProvider` + `SearchProvider` + `NewsProvider` + `StockProvider` + 对应 dataclass）。
> 新领域才新建接口；同领域换厂商**复用**现有接口。

### Step 2 — 读厂商 API 文档，先列「字段映射表」再写代码
不要边读边写。先把要用的 endpoint 和「厂商字段 → 领域 dataclass 字段」列成表，连同**坑**一起记在 provider 文件头注释里。例：

| 领域方法 | 厂商 endpoint | 厂商字段 → 领域字段 | 坑 |
|---|---|---|---|
| `search` | `/v5/place/around` | `pois[].name→name`、`location("lng,lat")→lng,lat` | 高德坐标是 **lng,lat**（经度在前） |
| `now` | `/v7/weather/now` | `now.temp→temp`、`now.feelsLike→feels_like` | 响应 `code=="200"` 才成功 |
| `air_quality` | `/airquality/v1/current/{latitude}/{longitude}` | `indexes[code=cn-mep].aqiDisplay→aqi`、`pollutants[].concentration.value→PM2.5/PM10` | 纬度在前、最多两位小数；该现行接口仅支持 JWT，响应不含 V7 的 `code/now` |
| `alerts` | `/weatheralert/v1/current/{latitude}/{longitude}` | `alerts[].headline/color.code/eventType.name/description/issuedTime→WeatherAlert` | 纬度在前、最多两位小数；仅支持 JWT，响应不含 V7 的 `code/warning`；保留所有当前生效预警（含台风等高风险天气） |
| `reverse_geocode` | 高德 `/v3/geocode/regeo` | `regeocode.formatted_address→天气卡地点` | 高德坐标顺序为 **lng,lat**；仅处理已经授权的坐标 |

### Step 3 — 写真实适配（`<vendor>.py`），HTTP 一律走 `_sdk/http.py`
- 构造 `self._http = AsyncHttpClient(vendor="<vendor>", service="<agent>")`。
- 每个厂商调用 `await self._http.get_json(url, params=..., op="<语义名>", headers=..., meta=meta)`。
- **厂商业务级错误**（HTTP 200 但 body 里 `status!="1"`/`code!="200"`）→ 在 provider 里判断并 `raise ProviderError(...)`。
- 空结果/字段缺失做防御（厂商常返回 `[]`/null）。
> 样板：`agents/navigation/src/providers/amap.py`、`agents/info/src/providers/qweather.py`。

### Step 4 — 工厂：按 env 选 real/mock + fail-fast + 决议日志/来源章（`__init__.py`）
统一走 `agents/_sdk/provenance.py`（契约 `docs/conventions.md` §9.4）：

```python
from agents._sdk.provenance import fail, log_resolution

def build_x_provider():
    vendor = (os.getenv("X_VENDOR", "mock") or "mock").strip().lower()
    if vendor == "<vendor>":
        key = os.getenv("<VENDOR>_KEY")
        if not key:
            fail("<domain>", "X_VENDOR=<vendor> 但 <VENDOR>_KEY 为空")   # 显式意图缺凭证 → 炸
        try:
            from .<vendor> import XProvider
            p = XProvider(key)
            log_resolution("<domain>", "<vendor>", True, p)   # 决议日志 + 给 p 盖来源章
            return p
        except Exception as e:
            fail("<domain>", f"XProvider 构造失败：{e}", e)    # 不再静默回退 mock
    elif vendor != "mock":
        fail("<domain>", f"未知 X_VENDOR={vendor}")
    m = MockXProvider()
    log_resolution("<domain>", "mock", False, m)              # mock 决议同样留痕
    return m
```
- `log_resolution` 输出统一行 `provider[<domain>]=<vendor>(real)|mock`（全栈
  `docker compose logs | grep "provider\["` 一屏审计），并给 provider 实例盖来源章
  （Step 6 的 `attach()` 出卡时读）。
- **严格栈**：`REQUIRE_REAL_PROVIDERS=on` 时 mock 决议在 `log_resolution` 处直接拒绝启动
  （豁免 `REQUIRE_REAL_EXEMPT`，默认 `parking,knowledge`）。
> 样板：`agents/navigation/src/providers/__init__.py`、`agents/info/src/providers/__init__.py`。

### Step 5 — 凭证经 env/secret，绝不进代码（红线，CLAUDE.md §5）
- 普通 key：`<VENDOR>_KEY` env，compose 里**只注入需要它的那个 Agent**（最小化，见 `deploy/docker-compose.yaml` navigation-agent / info-agent）。
- `.env.example` 补变量、**值留空、注释单独成行**（行内注释会被解析进值）。
- **JWT 类**（如和风）：私钥走 `*_PRIVATE_KEY_PATH`（文件路径，docker 挂载）或 `*_PRIVATE_KEY`（注入）；签发逻辑见 `qweather.py:QWeatherJWT`（Ed25519/EdDSA，token 本地缓存重签）。私钥不落盘日志、不进 commit。
  - 和风空气质量已不使用废弃的 `/v7/air/now`；必须请求 `/airquality/v1/current/{latitude}/{longitude}`，并使用 JWT `Authorization: Bearer <token>`。若项目仍仅配置旧 API Key，Provider 应明确报出 JWT 配置错误，Agent 仅降级空气质量区块，不影响天气主体。
  - 和风当前天气预警同样不走 V7：必须请求 `/weatheralert/v1/current/{latitude}/{longitude}` 并使用 JWT。返回体是 `alerts`，不是 V7 的 `warning`；预警请求失败时，卡片必须标明预警服务不可用，不能当作“暂无预警”。

### Step 6 — Agent 侧诚实降级 + 出卡盖 `_prov` 章（真实失败不阻断主链，也不造假）
- **运行期失败 → 诚实降级**：`try ... except ProviderError → return AgentResult(status=FAILED,
  speech="…暂时拿不到，稍后再试")`。**禁止** `self._fallback = MockXProvider()` 式运行期回退
  （旧模式，news/nearby 已拆除——真实源抖动时端上假数据的代价不对称）。
  失败由 provider span（`outcome=error`）在 Dashboard 可见。
- **出卡盖章**：凡展示外源数据的 ui_card，出卡处
  `attach(card, self.<provider>)`（`agents/_sdk/provenance.py`；写 `_prov={mode,vendor,fetched_at}`，
  HMI 渲染徽章）；降级路径显式传 `mode="degraded", note="原因"`（样板：stock 东财降级）。
  已推广卡族与「刻意不标」名单见 `docs/conventions.md` §9.3。
> 样板：`agents/info/src/handlers/weather.py::_weather`（诚实降级+盖章）、
> `agents/nearby/src/agent.py::_search`（诚实降级）、`agents/info/src/handlers/stock.py`（degraded 标记）。

### Step 7 — 测试（两层）
- **单测**：mock 掉 `provider._http.get_json`，喂厂商「黄金响应」，断字段映射/坐标顺序/错误码/降级。无需网络。
  > 样板：`agents/navigation/tests/test_amap_provider.py`、`agents/info/tests/test_qweather_provider.py`。
- **真冒烟**：`test/e2e_real_providers.py`——直连真实 API 验证集成与解析，**无 key 自动 skip**，断言识破「静默回退 mock」。新增 provider 时加一条同款。

---

## 3. `_sdk/http.py` 能力（你不用自己造轮子）
`AsyncHttpClient`（`agents/_sdk/http.py`）已统一提供：
- 按调用超时（默认 3s）、幂等 GET 有界重试+退避（仅超时/连接/5xx；4xx 不重试）；
- **每-provider 熔断**（连续失败 N 次→冷却期短路，半开探测）；
- 结构化异常 `ProviderTimeout` / `ProviderHTTPError` / `ProviderUnavailable`（基类 `ProviderError`，Agent 据此降级）；
- **provider 调用可观测**：每次调用 best-effort 发 `provider.<vendor>.<op>` span（带 `outcome/http_status/latency`、trace_id 取自 meta），复用 `observability/events.py`，自动进 collector→Dashboard 的 trace 视图。无 observability 包/NATS 时静默降级。

> 需要 POST/其他动词时在 `AsyncHttpClient` 加方法，**不要**在 provider 里直接用 httpx 绕开它。

---

## 4. 失败与降级矩阵（必须覆盖）

| 情况 | 处理 |
|---|---|
| 无凭证（默认 env，无显式意图）| `build_x_provider` 回退 mock，PoC 不阻断；决议日志留痕 |
| **显式 real 意图 + 构造失败/缺凭证** | **fail-fast 启动即炸**（`provenance.fail`），修配置而不是带假数据跑 |
| 严格栈 `REQUIRE_REAL_PROVIDERS=on` | 任何 mock 决议拒绝启动（豁免 `REQUIRE_REAL_EXEMPT`）|
| 厂商超时/5xx | `_sdk/http` 重试→仍失败抛 `ProviderError` → Agent **诚实降级说拿不到**（不回 mock）|
| 厂商业务错误（key 错/无结果）| provider 抛 `ProviderError`（4xx 不重试）→ Agent 诚实降级 |
| 降级到备选真实源（如 Tushare→东财）| 照常出数据，卡片 `_prov` 标 `mode=degraded` + note |
| 连续失败 | 熔断打开，冷却期直接降级，不持续打死外部 |
| 多 provider 协作部分失败 | `asyncio.gather(return_exceptions=True)`，缺项降级（见 trip-planner） |

---

## 5. 接入检查清单（PR 前逐项打勾）
- [ ] 业务只调领域接口，未直接 import 厂商 SDK / 未自写 `requests`/`httpx`
- [ ] real provider 经 `_sdk/http.py`；厂商业务错误转 `ProviderError`
- [ ] 工厂走 `provenance.fail/log_resolution`：显式意图下构造失败 fail-fast；real/mock 决议都留日志+来源章
- [ ] 工厂契约单测（默认 env→mock 不炸 / 显式意图×缺凭证或构造失败→raise，样板 `agents/*/tests/test_provider_factory.py`）
- [ ] 凭证经 env/secret，`.env.example` 补空占位（注释单独成行），未进代码/日志/commit
- [ ] Agent 侧 `ProviderError` **诚实降级**（FAILED+说拿不到），无运行期 mock 回退
- [ ] 外源数据卡出卡处 `attach()` 盖 `_prov` 章（conventions §9.3；备选真实源标 degraded）
- [ ] 单测（mock HTTP 黄金响应）+ `test/e2e_real_providers.py` 加真冒烟一条（无 key skip）
- [ ] compose 只给需要的 Agent 注入凭证（最小化）；新增依赖进 `agents/_sdk/requirements.txt`
- [ ] `python -m pytest --import-mode=importlib` 全绿；若动端侧 `python test/smoke_edge.py` 13/13

## 6. 反模式（出现即打回）
- ❌ Agent 里 `import 某厂商sdk` / 直接 `httpx.get`（绕开适配层与熔断/可观测）
- ❌ 凭证写进代码/默认值/日志，或多行私钥直接堆进 `.env`（用文件路径或 `\n` 单行）
- ❌ 真实失败时抛裸异常让主链 500，而非降级（破坏「外部抖动不击穿」）
- ❌ **运行期真实源失败改供 mock 假数据**（`self._fallback = MockXProvider()` 旧模式——
  news/nearby 已拆除；假 POI 可能被导航过去）
- ❌ 显式配置了真实源却在构造失败时静默回退 mock（2026-07-17 起 fail-fast，参考 ASR 那次教训）
- ❌ 外源数据卡不带 `_prov`（真实性对评审/演示/badcase 排查不可见）
- ❌ 新厂商改了 Agent 对外契约（Provider 切换必须对 Agent/编排无感）
