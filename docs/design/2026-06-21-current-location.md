# 当前定位能力设计

## 目标

让座舱助手在用户主动授权后，用当前精确位置完成天气、导航、附近餐饮/停车/充电等业务；位置不进入会话记忆、设置持久化或日志。

## 数据流与边界

```
HMI 点击“使用当前位置”
  → 浏览器定位授权
  → 单次请求 meta(current_lat/current_lng/accuracy/time/source)
  → Cloud Engine 仅在 granted_scopes 含 location.read 时转发
  → Agent Provider（天气 / 高德 POI / 餐饮 / 停车 / 充电）
```

- HMI 在 localStorage 中仅持久化“允许使用当前位置”的开关，不持久化精确坐标；后续启动会重新向浏览器获取当前位置。关闭开关立即停止位置透传并清除内存坐标。
- 精确坐标只随当前请求发送，`PlanContext.prefs` 不包含未获 `location.read` 授权的数据。
- SDK 统一校验 WGS-84 范围（纬度 `[-90, 90]`，经度 `[-180, 180]`），非法值忽略。
- 城市名称仍优先于坐标；未给城市时，天气以 `lng,lat` 调用和风 GeoAPI；导航与周边服务以精确坐标做 nearby/origin。
- 天气卡不展示坐标：已配置 `AMAP_KEY` 时，info-agent 以高德逆地理编码把坐标转换为格式化地址；无该凭证时安全降级为“当前位置”。
- 天气扩展区块复用已授权坐标：和风空气质量和当前生效预警分别调用 `/airquality/v1/current/{latitude}/{longitude}`、`/weatheralert/v1/current/{latitude}/{longitude}`；两者均用 JWT，预警失败不阻断天气主体，并在卡片中标明服务不可用而非误报“暂无预警”。

## 交互

设置页新增“定位权限”区块。用户点击“申请并启用”才会首次触发浏览器权限弹窗；启用后应用在后续会话自动刷新当前位置。关闭只撤销座舱助手对位置的使用，浏览器级权限需由用户在浏览器站点权限中撤销。

## 验证

- 单测：HMI 坐标序列化、SDK 解析与范围校验、Cloud scope 门控、天气和导航坐标优先级、周边餐饮/停车坐标传递。
- 集成：重建 HMI、云规划和受影响 Agent；通过 HMI 请求带坐标的天气/附近 POI，确认链路可用。
