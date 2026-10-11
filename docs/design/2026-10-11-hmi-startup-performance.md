# HMI 首屏加载修复（B04，2026-10-11）

状态：已按用户单独批准更新基础设施配置并发布，B04 通过线上复验；当前发布与独立验证统一见 [QA 交接 §2](../reviews/2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界)。

## 原因与方案

[线上初验](../reviews/2026-10-11-hmi-visual-v2-browser-audit.md) 的两次新浏览器页面就绪分别为 127.1 / 107.5 秒。资源记录包含 94 个资源请求、约 5.33 MB，入口是 `/@vite/client` 与 `/src/main.tsx`；云端 Compose 明确启动 `npm run dev`。开发模块、React 调试代码和源码映射经私网逐项传输，放大了链路耗时。

本批将云端 HMI 改为提供构建产物：

1. 容器启动后，用已有运行时 `VITE_EDGE_GATEWAY_URL`、`VITE_AUDIO_API_URL`、`VITE_WS_TOKEN` 执行 Vite build，再打开 HTTP 监听。构建失败即启动失败，不能回退到旧 dist。凭据不作为 Docker build args，不写入镜像构建层。
2. 由现有 Node 运行时提供 `dist` 内文件，gzip 压缩、带 hash 的资源私有长期缓存；HTML 不缓存，未带 hash 的模型/字体重验证。没有新增 npm 依赖。
3. 文件从构建目录清单读取；拒绝未列入清单的源码、dotfile、穿越路径、非法 Host 和写方法。保留原 COOP/COEP 及 WASM MIME 类型。
4. 高德配置/固定路径代理继续复用 `server/amap.mjs`。JS Key 的既有分发方式不变，安全密钥只在服务端使用。
5. 本地开发仍运行 Vite。云端 `command` 及云端 Dockerfile 的缺省命令改为 `npm run serve`，端口、模型文件、挂载、后端地址、权限与其他服务不变。

Vite 5 的[部署说明](https://v5.vite.dev/guide/static-deploy)将 `vite preview` 定位为本地预览。本批以 Node 静态服务承接构建产物，不把 preview 命令作为云端服务。

`hmi/src/types.ts`、整个 `App.tsx` 以及 UI/语音状态机均未修改。本批没有做懒加载或改交互时序。

## 性能证据

同一台 Windows 主机、Edge、1920×1080；3 次成对测试，禁用浏览器缓存；CDP 固定下载 262144 B/s（约 2.1 Mbps）、延迟 150 ms。两种入口都由本地 Vite 承载，其中构建入口直接使用待发布的同一个 Node HTTP handler；业务 WebSocket 在浏览器内隔离。

| 中位数 | 开发入口 | 构建入口 |
|---|---:|---:|
| 界面就绪（400 ms 轮询） | 21.356 s | 1.833 s |
| 请求数（含 HTML） | 95 | 6 |
| 传输字节（含响应头） | 5,387,709 | 298,036 |

约减少 94.5% 传输字节、93.7% 请求、91.4% 就绪时间。这是受控网络的本地对照，不是线上私网修复后的读数；不能把线上 127 秒与本地 1.83 秒直接拼成收益。

可复现入口：两个终端依次运行以下脚本。只启动 Vite，不启动本地 Compose/后端；测试用无效后端域名和假客户端凭据，不读真实凭据。两台测试 Vite 使用 5194/5195，浏览器 CDP 使用 9339，不与其它同端口测试并行。

```powershell
node test/hmi_cdp/startup_servers.mjs
```

```powershell
$env:HMI_VISUAL_URL='http://127.0.0.1:5194'
$env:HMI_VISUAL_BATCH='startup-performance-current'
node test/hmi_cdp/startup_performance.mjs
```

证据：`.artifacts/hmi-visual-v2/cold-load-20261011-reproducible/performance.json` / `summary.json`。测试服务器会构建 `hmi/dist`；不要与另一个依赖同一 dist 的预览同时构建。首版对照发现 Vite build 会设置进程 NODE_ENV，已将独立开发入口恢复为 development 后重跑；旧校准读数不作结论。

## 功能与边界验证

- HMI **420 passed、0 failed**；TypeScript 通过，构建约 1.6 秒。保留原有大于 500 kB bundle 提示，不通过放宽警告门槛隐藏体积。
- 云端部署资产检查 **205 passed、1 skipped**，跳过的是 Docker Redis 探针；没有启动本地 Compose 或后端。
- 构建产物入口：滚动 8 组 + 3 次尺寸变化、地图层级 8 组、固定确认/到期/历史保护等原对话回归通过。
- 声音资源：Silero ONNX、KWS 4 个文件、ORT WASM 共 6 项，浏览器取回后与本地字节 SHA-256 一致；两份 WASM 均在浏览器成功编译，`crossOriginIsolated=true`、`SharedArrayBuffer` 可用。麦克风调用 0；这不是真人唤醒或声学验收。
- 新 HTTP 测试覆盖 gzip/拒绝 gzip、HEAD、ETag、cache、Host/路径/方法边界、高德接口保密边界、失败构建、运行时公共配置进入构建而安全密钥不进入构建。
- 构建入口的固定确认条等测试隔离了业务；它们不证明真实付款/车控、云端记忆删除或后端模型效果。

功能证据在 `.artifacts/hmi-visual-v2/cold-load-built-{scroll,map,conversation}/`、`cold-load-voice-assets/`；单测日志在 `.artifacts/hmi-cold-load-20261011/`。

## 已授权的云端改动

受控路径只有 `deploy/cloud/compose.cloud.yaml`（HMI 启动命令）与 `deploy/cloud/hmi.Dockerfile`（同一缺省命令）。实际共享安装项是 Compose override；原 `/app/vite.cloud.config.mjs` 挂载继续用作 build 配置，Node 服务沿用相同 Host 环境变量。

未修改 `.env`、密钥、数据库、CI/CD workflow、Tailscale、安全组、systemd 或保留策略。按 [开发指南的基础设施批准锚规则](../dev-guide.md#基础设施批准锚)，先生成精确提交的只读材料；用户明确批准“配置、推送、部署及复验”后，应用摘要 `b3d94a4157ac5e60942806fe03cd8c0a4aba71f69a3bbaf12c32986f22131d70`。锚更新后的 deploy dry-run 无阻塞，随后执行部署。材料与执行结果在 `.artifacts/hmi-cold-load-20261011/approval/`、`.artifacts/hmi-cold-load-release-20261011/`。

旧镜像没有 `serve` 入口；若回退本批，旧 release 必须配套恢复本次基础设施备份中的 Compose 与批准锚，不能让旧镜像使用新启动命令。

## 线上复验（2026-10-11）

验证对象固定为 `32bd007c03a748fa7eddd8a10f409a57f4a8bf0f`。本批推送只包含这一条提交；相对上一发布还含 main 已有的 Android 构建工具与文档提交，未代提交其他任务的未提交文件。CI、独立 status/verify 的当前权威字段见 QA 交接 §2。

- **三次独立 Edge 进程、独立 profile/CDP 端口、禁用缓存**，使用实际私网链路而非限速模拟：UI 就绪 **1.012 / 1.013 / 1.014 s**（400 ms 轮询，中位数 **1.013 s**）；DOMContentLoaded 为 **0.727 / 0.724 / 0.695 s**。均为 **6 请求**，传输中位数 **296,573 bytes**。这是本轮样本，不代表任意链路的延迟保证；启动测量隔离业务 WebSocket。
- 真实线上页面 + 真实 WebSocket：三轮两城市天气均 final、零 action、完成后距底部 0；主动读历史保持位置、点击胶囊才回到底部；中断后下一轮恢复；真实杭州路线的深浅地图返回与设置遮盖通过。前后 active 均为 `minimax / MiniMax-M3.1-Flash-Preview`，未切换。浏览器未捕获运行时/网络错误。
- 业务隔离的真实地图 SDK 矩阵 **8 组**通过；声音资源 **6 项** gzip、字节指纹核对通过，两份 WASM 在浏览器编译成功，跨源隔离保持，麦克风调用 0。ORT 按线上主脚本实际引用取文件，并和运行镜像的 SHA-256 对账；线上依赖解析为 **1.30.0**，未借用本地构建的 ORT 文件名/指纹。
- 线上 HTML 不缓存，hash 资源 private/immutable，JS 为 gzip；源码/HMR/dotfile 路径 404；高德 JS Key 与授权输入一致，安全密钥未出现在 HTML/主脚本。没有改契约、交互或硬件验收范围。
- 驱动校准明确排除：一次浏览器端口复用导致空白页启动失败、一次 eval 指令格式错误（尚未执行业务轮）、一次拿本地 ORT hash 请求线上而 404。均修正探针后重跑；三次最终加载使用不同进程与端口，ORT 最终取线上实际引用。未放宽产品断言，未将校准失败算成通过。

证据主目录 `.artifacts/hmi-cold-load-release-20261011/`：`release-summary.json`、`cold-start.json`、`online-result.json`、`map-layer-evidence.json`、`map-voice-result.json`、`image-wasm-inventory.json`、`http-boundaries.json`、`calibration-exclusions.json`；地图与声音浏览器证据另在 `.artifacts/hmi-visual-v2/b04-online-map-voice/`、`b04-online-voice-verified/`。B04 在本轮覆盖范围内已修复；真人麦克风、唤醒声学与实车仍不在此次验收内。
