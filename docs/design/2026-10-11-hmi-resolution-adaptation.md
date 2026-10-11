# HMI Visual v2 分辨率适配（2026-10-11）

状态：候选修复与本地浏览器验证已完成，单独提交；尚未获得本批推送/部署批准。当前发布统一看 [QA 交接 §2](../reviews/2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界)。

## 问题与设计依据

用户报告 3440×1440 宽屏的“布局比例、留白不合适”。100% CSS 视口下已复现：对话仍固定在 x=24、宽 800；舞台内容区从 1024 拉到 2544 宽，车况摘要却留在左侧；设置正文仍从 x=472 开始、宽 1120，右侧空出约 1848 像素。

本批按 [brief §10](2026-10-08-hmi-visual-redesign-brief.md#10-自适应与行车) 和已冻结的四档 Size token 适配。Figma MCP 只读 `QNXzATLf4WOKLD1rV1dilp` 的 `34:9`（外壳/舞台）与 `54:9`（设置），复用现有光球、图标、字体和控件，没有新资产或 UI 依赖。Figma 的 1920 画板用于基准对照，以下超宽规则是代码适配，不声称存在一张 3440 设计稿。

## 修复

1. **前景居中，背景铺满**：交互区上限 2240，由同一个布局 inset 驱动状态栏、对话、舞台安全区和地图控件。3440 下对话 x=624，舞台安全区宽 1344；保留原字号、触控与四档面板宽度，不按屏幕像素放大文字。
2. **阅读内容限宽**：超宽屏车况摘要在舞台安全区居中；天气、日程、阅读等内容上限 1120；付款码保留 720 上限。设置导航和内容作为一个 1920 宽的区域居中，正文仍为 1120 上限。1920 基准的外壳位置与尺寸保持。
3. **地图取景与标记尺寸同步**：读取实际面板、顶栏边界，以及标记半径和选择描边，计算相机留白；至少保留 360 宽的地图区域。实测 SDK 的中心偏移没有跟随 CSS 字号/行车尺寸更新，改为左上锚点加 CSS 百分比居中，使标记中心随实际尺寸变化。锚点定义参照[高德 JS API 文档](https://developer.amap.com/api/maps-javascript-api/reference/amap-marker/marker)。地图仍使用服务端原坐标/轨迹，点选仍是本地视图操作。
4. **补齐尺寸变化后的底部跟随**：仅增加内容内边距时，默认 content-box 监听不触发；独立实验等待 800 ms 后仍距底部 220 px。内容容器改监听 border-box，修后同一实验距底部 0；主动读历史继续不强制滚底，沿用已批准的 D12。

本批未修改 `types.ts`、`App.tsx`、业务契约、语音状态机、依赖或部署配置。集成基线为共享 main 已有的 `eb098edd`（语义打断）；快进同步前后的所有未提交文件逐一核对字节一致，其他任务的改动不纳入本批提交。

## 验证与证据

最终证据须与提交的 HMI blob 指纹对账，见 `.artifacts/hmi-resolution-20261011/integrated-source.json`。

| 检查 | 结果与边界 |
|---|---|
| HMI / TypeScript / build | 集成基线上的 **445 passed、0 failed**；typecheck、build 通过。保留已有大 bundle 提示。 |
| 分辨率矩阵 | **9 种视口 × 深浅 × 标准/大字 × 5 个场景 = 180 组**通过：待机、对话、设置、地图、行车地图。负向注入“面板重新贴左”被检测器拒绝。 |
| 视口 | 1920×1080、1920×720、1920×1200、2560×1440、3440×1440、3840×2160、5120×1440；另有 2752×1152 @1.25、2293×960 @1.5，模拟 3440 屏幕 125%/150% 的等效 CSS 尺寸和 DPR。使用真实 Edge/CDP，未改变系统或用户浏览器设置。 |
| 动态变化 | 不刷新页面，连续调整 5 次窗口尺寸、2 次实际字号设置，并在变化后通过鼠标点击 7 次地图标记；全部通过，零真实业务出帧。 |
| 地图 SDK | 9 场景及缺坐标、取消、编号保序、失败/重试通过；实际 SDK/瓦片，业务 WebSocket 隔离。此轮未单独重做安全密钥内容扫描，不转借旧发布的检查。 |
| 滚动 | 最终集成回归 **8 个底部跟随/历史场景、3 次窗口变化**通过；独立 padding 检查距底部 0，历史阅读下迟到的尺寸变化不移动阅读位置。 |

测试历史分开保留：

- 线上旧版的宽屏探针实际报出 5 条布局失败；首次适配矩阵 180 组中还发现 50 条标记边界失败，修正尺寸/锚点后完整重跑。
- 一次滚动检查暴露 padding 漏报；串行旧用例偶尔被同时发生的窗口变化掩盖，拆开观察后确认 content-box 没有通知、border-box 有通知，并在产品中修复。
- 旧地图探针只拦截 `localhost:8090/ws`，在本轮测试后端地址下超时，未进入地图断言；探针改为按 `/ws` 路径隔离，并增加可指定输出目录，修正后重跑。这是驱动校准，不计产品通过。
- 早期 `c705f8c4` 基线的 422 项读数与截图不转借给集成基线；同步已有语义打断代码后重新执行 HMI 检查和浏览器矩阵。

最终浏览器证据在 `.artifacts/hmi-visual-v2/resolution-integrated-20261011/`、`resolution-live-resize-integrated-20261011/`、`resolution-scroll-integrated-20261011/`；SDK 在 `.artifacts/hmi-resolution-20261011/map-sdk-integrated/`。这证明浏览器布局/交互，不代表真人麦克风、实车或后端模型旅程已验收。

## 3440 截图

| 场景 | 修前线上页面（业务隔离） | 修后本地 Vite（业务隔离） |
|---|---|---|
| 待机 | [修前](../../.artifacts/hmi-visual-v2/resolution-before-20261011/3440-1440-idle.png) | [修后](../../.artifacts/hmi-visual-v2/resolution-integrated-20261011/3440-1440-1-dark-normal-idle.png) |
| 对话 | [修前](../../.artifacts/hmi-visual-v2/resolution-before-20261011/3440-1440-conversation.png) | [修后](../../.artifacts/hmi-visual-v2/resolution-integrated-20261011/3440-1440-1-dark-normal-conversation.png) |
| 地图 | [修前](../../.artifacts/hmi-visual-v2/resolution-before-20261011/3440-1440-map.png) | [修后](../../.artifacts/hmi-visual-v2/resolution-integrated-20261011/3440-1440-1-dark-normal-map.png) |
| 设置 | [修前](../../.artifacts/hmi-visual-v2/resolution-before-20261011/3440-1440-settings.png) | [修后](../../.artifacts/hmi-visual-v2/resolution-integrated-20261011/3440-1440-1-dark-normal-settings.png) |

复现使用本地 Vite（地图项需既有高德配置），设置 `HMI_VISUAL_URL`、`HMI_VISUAL_BATCH` 后执行 `node test/hmi_cdp/visual_resolution.mjs`；默认全矩阵，`HMI_RESOLUTION_SMOKE=1` 仅跑 3440 标准深色。与其它占用 CDP 9339 的视觉脚本串行运行。地图专项可设 `HMI_MAP_URL`、`HMI_MAP_OUTPUT` 后执行 `node test/hmi_cdp/map_stage.mjs`，使用独立 CDP 9371。
