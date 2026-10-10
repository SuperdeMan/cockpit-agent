# HMI 真实地图接入（2026-10-10）

状态：2026-10-10 经用户批准完成凭据导入、Compose 批准、精确 SHA 推送与生产发布，独立 verify 和线上真实地图专项通过。当前 release 与统一证据见 [QA 交接 §2](../reviews/2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界)。开发阶段仅进程注入凭据，授权后才写入根 `.env` 与云端受控配置。

## 范围与设计依据

- 延续 Visual v2 的地图舞台，复用 Figma `QNXzATLf4WOKLD1rV1dilp` / `37:154`（S-02 路线规划）的已读高保真上下文与截图。底图组件标明实施期换成地图 SDK；地图区域使用真实数据绘制，不把设计中的杭州底图 SVG 当地图瓦片。
- 高德 JS API 2.0 按需加载；泊车为全幅底图、左侧会话面板、实色标题与地点摘要，取景避让浮层。深色使用官方 `dark`，浅色 `whitesmoke`；路线与编号使用项目 token。底图实际路网与官方配色不声称逐像素等同设计占位图。
- `poi_list`、`poi_detail`、`place_list`、`place_detail`、`route_plan`、`charging_route`、`trip_itinerary` 只读原卡片；`[lat,lng]` 转成 SDK 的 `[lng,lat]`，缺失 / 非数 / 越界 / `0,0` 不画，行程只画 grounded 的 POI。保留原列表编号，不对缺坐标行重新编号；路径损坏不跨缺口连线，超长按现有客户端上限 400 点采样并保留端点。
- Figma 示例的多条备选路线、红绿灯数量没有现有契约字段，本批不生成这些数据或选择动作。起点标「起」，不当作实时车位；没有轨迹时只画真实地点，不调用浏览器 SDK 重新规划。卡片仍按原上行与确认链路处理，`types.ts` 和 `App.tsx` 原文未改。
- 行车时只展示地图与既有回答条，关闭地图拖拽、缩放、地点交互；取消导航后销毁地图，不恢复旧路线。SDK/底图失败可重试，缺坐标时不加载 SDK，回退画面明确标注「示意」。

## 凭据与服务端代理

- 两个新运行时项：`AMAP_JS_KEY`、`AMAP_JS_SECURITY_CODE`。不复用或替换后端 Web服务 `AMAP_KEY`，不写入 `.env.example`、前端 `VITE_*`、源码或构建产物。
- `/api/maps/config` 只返回可用状态及 JS API 的应用 Key，禁止缓存。JS API 必须在浏览器请求中使用应用 Key；配套安全密钥从不下发。
- `hmi/server/amap.mjs` 为现有 Vite dev/preview 增加服务端代理，生产现有云端 Vite 配置继承此插件；未新增依赖、监听端口或后端服务。
- 代理限 GET、同源浏览器请求及固定地图初始化 / 样式路径；上游固定 `restapi.amap.com` 或 `webapi.amap.com`，绑定配置中的 Key，覆盖调用者的 `jscode`，不接受搜索、路线规划或任意 URL 转发。设 10 秒超时与 2 MiB 响应上限，上游错误不带 URL / 密钥；检测到响应包含安全密钥时拒绝下发。
- 启动器读取根 `.env` 的这两个项并脱敏输出，Compose 提案只给 HMI 增加两项环境变量。未改云端 infrastructure 锚、CI/CD、数据库或商户配置；实际写入运行配置与部署仍按项目红线授权。
- 高德官方依据：[申请 Web端 Key](https://lbs.amap.com/api/javascript-api-v2/prerequisites)、[安全密钥代理](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)、[视野避让](https://lbs.amap.com/api/javascript-api-v2/guide/map/state)。

## 验证与边界

- 真实 SDK 初始化、瓦片、样式及图标请求成功；浏览器中 `window._AMapSecurityConfig` 只有 `serviceHost`，网络与 CDP 检查没有安全密钥原值，业务出帧为 0。
- 浏览器覆盖 9 组：深浅路线、浅色大字 1920×720、地点列表、充电、行程、缺轨迹路线、行车大字及失败重试；另验缺坐标零 SDK 请求、取消销毁、编号保序、点选与换主题后的选中状态。标记须在视口内且避开会话 / 标题 / 摘要。大字行车首次读出终点裁切 1px，增加取景边距后同断言通过，未放宽断言。
- 纯函数测试覆盖坐标 / 轨迹 / 取消 / grounded 与取景；代理测试覆盖路径与 Key 绑定、方法 / 跨站 / 缺配置拒绝、超限响应与错误不泄密。启动器测试覆盖凭据脱敏。
- 复现：凭据只注入 Vite 服务进程后启动本机 Vite；`HMI_MAP_URL` 指向该 Vite，运行 `node test/hmi_cdp/map_stage.mjs`。测试进程可通过 `AMAP_JS_SECURITY_CODE` 做不泄露断言，该值不输出，启动浏览器子进程时剔除两项凭据环境变量；`secretChecked=false` 时不得宣称完成实际密钥检查。
- 本地验证：HMI **401 passed**，严格类型检查与 Vite build 通过（已有 >500 kB bundle 提示保留）。启动器 / Compose / 发布资产检查首次 **389 passed / 4 skipped / 1 failed**；唯一失败来自并发合入 README 后权威入口语句不再匹配既有守卫，按相同含义恢复原表述后该项专项 **1 passed**，没有放宽断言或跳过测试。
- 本地证据目录 `.artifacts/hmi-map-20261010/`：`hmi-tests.log`、`typecheck.log`、`build.log`、`python-tests.log` / `docs-recheck.log`、`browser-checks.json`、各场景截图与 `geometry-*.json`、`confidentiality.json`。源码 / 构建 / 文本证据未检出凭据原值，扫描器以仅在内存中插入的凭据及 UTF-16 编码做反向验证；`types.ts` / `App.tsx` 整文件无改动。不转借旧版本后端全量、生产 verify 或车机硬件验收。
- 这是真实地图展示接入，不新增定位采集、逐向导航、实时车位、路线重算或服务端执行语义。本次生产域名与云端瓦片加载已独立验证；设备 GPU/触控仍需设备验收。

## 授权发布（2026-10-10）

- 用户明确批准两项运行时凭据、Compose 透传、推送与部署。只写 `AMAP_JS_KEY` / `AMAP_JS_SECURITY_CODE`，保留其他字段、原 `AMAP_KEY` 与文件权限，回读与授权输入一致；凭据经进程内读取和 SSH stdin 传递，不进命令参数或日志。云端事务锁占用时未改配置，等待释放后完成写入。
- 前置导航提交由其任务同步并发布后，本批以实际基线重新 dry-run。Compose 一次性批准摘要 `1472e6a003ebfbc31ef77aa20957cb527ef9e8bc008a83137a98f636633ebe39`，零阻断；干净隔离工作树未复制 `.env`。26 个镜像与发布事务完成，随后独立 status / verify 通过。
- 线上运行时接口只返回应用 Key，可用状态 / 输入匹配 / `no-store` 均已检查；安全密钥未下发。实际页面资源加载高德 SDK 与瓦片，9 组地图浏览器断言通过，业务出帧 0、页面异常 0；浏览器启动器隔离凭据环境变量后再次通过，前后运行版本相同。
- 线上测试使用合成地图卡和隔离 WebSocket，不生成真实导航、车控、下单或付款。浏览器驱动的路径 / 端口 / WebSocket 目标按线上地址适配，断言不放宽；测试驱动与文档后续提交不是生产代码 SHA。
- 证据：`.artifacts/hmi-map-20261010/credential-configuration.json`、`approved-dry-run.stdout.log`、`apply-result.json`、`live-verification.json`、`status-final.json`、`online/`、`browser-environment-recheck.json`。精确 release、Provider / 模型、统一 verify artifact 与容量仅维护在 QA 交接 §2。
