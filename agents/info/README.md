# info Agent — 信息助手

天气 + 联网搜索 + 赛事 + 新闻 + 股票的只读信息聚合。所有真实 provider 凭证经 env 注入，
无凭证或调用失败时降级（搜索/赛事/股票降 mock；搜索还会按链路逐级降级），不击穿主链。

## 能力
| intent | 说明 | slots |
|---|---|---|
| `info.weather` | 实时天气（指定城市或当前定位）| city, date |
| `info.forecast` | 未来几天天气预报 | city, days |
| `info.alerts` | 天气预警 | city |
| `info.indices` | 生活指数（运动/洗车/紫外线…）| city |
| `info.air_quality` | 空气质量（AQI/PM2.5…）| city |
| `info.search` | 联网搜索（含概念解释/信息查询）| query, limit |
| `info.sports` | 赛事实时比分/赛程（足球）| query, league |
| `info.news` | 新闻速览（编号列表 + 逐条一句话）| topic, limit |
| `info.stock` | 股票/指数行情 | symbol |

> 天气无 city 且无定位 → `NEED_SLOT` 追问；真实 provider 失败**诚实报错、不返回 mock 假数据**。

## 搜索 / 新闻 / 赛事的设计（2026-06-22 重构）

「座舱 AI 的本质是提炼内容 + TTS 播报」，不是给一堆要点开的链接。详见
[`docs/design/2026-06-22-search-quality-and-card-redesign.md`](../../docs/design/2026-06-22-search-quality-and-card-redesign.md)。

- **搜索**：Exa 正文级检索（→AnySearch→Bing→mock 降级）→ **接地合成**（喂正文、强制来源引用、
  **无依据即诚实弃权，不编造**）。气泡给结论、`search_result` 卡只给证据（来源/时效/置信度），不复读。
- **赛事**：命中已知赛事 + 意图词 → api-football **结构化真实比分**（按日期查+客户端按 league_id 过滤，
  免费档可用；队名英→中映射 + 国旗）。不经 LLM，杜绝比分编造；查不到诚实回落通用搜索。
- **新闻**：Exa 优先（正文+时效，去重、过滤首页/错误页）→ serpapi(Google/Baidu)→AnySearch→mock。
  **一次 LLM 调用**产「总览 + 逐条一句话」；语音/气泡播报编号 1~10 速览，卡片给可点开来源。
- LLM 合成经 llm-gateway，模型由 env 决定（项目默认 MiMo，换服务商见 `.env.example` 的 `LLM_*`）。

## Provider 适配
```
src/providers/
  base.py             各 Provider 接口 + dataclass（Weather/SearchResult/SportsFixture/NewsItem/Quote）
  mock.py             各 Mock Provider（PoC / 离线 / 单测）
  qweather.py         QWeatherProvider（和风天气，JWT/EdDSA）
  search_exa.py       ExaSearchProvider（联网搜索主，contents.text 正文级）
  search_any.py       AnySearchProvider（搜索兜底 + MCP extract 正文补抓）
  search_bing.py      BingSearchProvider（搜索再降级）
  news_serpapi.py     SerpApiNewsProvider（新闻兜底：Google/Baidu News → AnySearch）
  sports_apifootball.py  ApiFootballProvider（赛事比分/赛程）
  stock_tushare.py / stock_eastmoney.py  股票（A股 / 港美股降级）
  amap_geocoder.py    坐标→可读地址逆地理编码
  __init__.py         build_*_provider()：按 env 选 real/mock，失败逐级降级
```

切换真实厂商（和风天气）。`QWEATHER_HOST` 填控制台 API Host（如 `xxxx.qweatherapi.com`）。
鉴权二选一，**JWT 优先**：

```bash
# 显式设置推荐；完整 JWT / API Key 凭证也会自动启用真实 Provider
WEATHER_VENDOR=qweather
QWEATHER_HOST=<你的 API Host>
# (A) JWT（和风新版，推荐）：Ed25519 私钥本地签发 JWT，Authorization: Bearer
QWEATHER_PROJECT_ID=<项目ID(sub)>
QWEATHER_KEY_ID=<凭据ID(kid)>
QWEATHER_PRIVATE_KEY=<Ed25519 私钥单行 PEM 或裸 base64（换行用 \n）>
# 或 QWEATHER_PRIVATE_KEY_PATH=<容器内可访问的 Ed25519 PEM 文件路径>
# (B) API Key（旧版，与 JWT 二选一；仅适用于仍支持 API Key 的 V7 天气接口）
# QWEATHER_KEY=<你的 key>
```

空气质量与天气预警均使用和风现行 JWT 接口：`GET /airquality/v1/current/{latitude}/{longitude}` 和 `GET /weatheralert/v1/current/{latitude}/{longitude}`。预警字段映射为卡片的标题、等级、类型、正文和发布时间；无预警时仍显示“暂无天气预警”。若仅配置旧 API Key，天气主体仍可用，空气质量与预警作为可选区块降级。接口文档：[实时空气质量](https://dev.qweather.com/docs/api/air-quality/air-current/)、[天气预警](https://dev.qweather.com/docs/api/warning/weather-alert/)。

未配凭证或调用失败时自动回退 mock，PoC 不阻断。provider 调用经 `_sdk/http.py` 统一
超时/重试/熔断，并 best-effort 发 `provider.qweather.*` span 到 Dashboard。
JWT 用 Ed25519/EdDSA 签名（依赖 `cryptography`），token 短期有效、本地缓存重签。

## 测试
```bash
python -m pytest agents/info/tests --import-mode=importlib -q
```
