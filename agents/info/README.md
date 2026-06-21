# info Agent — 信息助手

提供信息类查询能力。当前实现**实时天气**（`info.weather`），预留 news / calendar / reminder。

## 能力
| intent | 说明 | slots |
|---|---|---|
| `info.weather` | 查询指定城市或当前位置的实时天气 | city, date |

## Provider 适配
```
src/providers/
  base.py        WeatherProvider 接口 + Weather dataclass
  mock.py        MockWeatherProvider（PoC / 离线 / 单测）
  qweather.py    QWeatherProvider（和风天气真实适配，凭证经 env）
  __init__.py    build_weather_provider()：按 env 选 real/mock，失败回退 mock
```

切换真实厂商（和风天气）。`QWEATHER_HOST` 填控制台 API Host（如 `xxxx.qweatherapi.com`）。
鉴权二选一，**JWT 优先**：

```bash
WEATHER_VENDOR=qweather
QWEATHER_HOST=<你的 API Host>
# (A) JWT（和风新版，推荐）：Ed25519 私钥本地签发 JWT，Authorization: Bearer
QWEATHER_PROJECT_ID=<项目ID(sub)>
QWEATHER_KEY_ID=<凭据ID(kid)>
QWEATHER_PRIVATE_KEY_PATH=<Ed25519 私钥 PEM 路径>   # 或 QWEATHER_PRIVATE_KEY 单行 PEM(换行用 \n)
# (B) API Key（旧版，与 JWT 二选一；仅适用于仍支持 API Key 的 V7 天气接口）
# QWEATHER_KEY=<你的 key>
```

空气质量使用和风现行接口 `GET /airquality/v1/current/{latitude}/{longitude}`，需要 JWT；若仅配置旧 API Key，天气主体仍可用，空气质量会按可选区块降级为 mock/空数据。接口文档：[实时空气质量](https://dev.qweather.com/docs/api/air-quality/air-current/)。

未配凭证或调用失败时自动回退 mock，PoC 不阻断。provider 调用经 `_sdk/http.py` 统一
超时/重试/熔断，并 best-effort 发 `provider.qweather.*` span 到 Dashboard。
JWT 用 Ed25519/EdDSA 签名（依赖 `cryptography`），token 短期有效、本地缓存重签。

## 测试
```bash
python -m pytest agents/info/tests --import-mode=importlib -q
```
