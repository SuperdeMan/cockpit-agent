# HMI Visual v2 · 后续验证与发布准备

- 日期：2026-10-09。
- 起点：`a49662fa69644566de6527406601cf0d0ac6e644`，I1–I6 已合入，未部署生产。
- 用户明确要求三项都做：视觉/交互残项复查、22 个既有 TypeScript 错误清理、生产发布准备；实际部署另行授权。
- 继续保持 `types.ts` 与 `App.tsx.handleEvent` 原文不变，不加 UI 依赖，不改后端语义。共享工作树的其它改动不纳入。

## T1 · 类型清理

- 提交/推送：`3e2da487e68522e0e248fab082249187cfa64345`。
- 精确复现 22 项：19 处缺少 `.mjs` 声明（16 个模块）、`airQualityBadge` 声明漏项、2 处 PCM typed-array 与 `BlobPart` 不兼容。
- 按实现补齐模块 API 声明，网络帧使用 `unknown`；没有通配 `declare module`、`@ts-ignore` 或放宽 tsconfig。加入 `npm run typecheck`，共享协议文件未改。
- 声纹注册与自证把 `Int16Array` 的当前视图复制为自有缓冲再交给 Blob；采样范围、格式、乘员 ID、认证和请求路径保持原样。用实际上传函数的隔离测试验证普通/共享缓冲、带偏移的视图、原 Blob 输入与源数据不变。
- Android S2S 构造既有自引用回调补一个显式类型，避免新增声明引出循环推断；无运行时逻辑改动。共享 WebSocket 的 `wake()` 也按已有实现声明。
- HMI 严格类型检查 **0 错误**，HMI **388 passed**，Vite build 通过；Android 类型检查前后均 **0 错误**，受影响的语音/网关/播放/共享准入 **64 passed**。Jest 有退出延迟提示，最终退出码为 0；不将其掩盖为没有提示。
- 原 I6 的 22 项读数保留为历史证据，不回写旧测试结果；本批证据存 `.artifacts/hmi-visual-v2/followup-types/`。

## T2 · 视觉与交互复查

- 提交/推送与发布候选：`6280cdb0ddd8291440e83a90aab693dfc9bcd522`。
- 新增 `visual_accessibility.mjs`：从实际面板读取 **734px** 内容宽，44 份卡片 × 深浅的大字档共 **88 组**无横向溢出；1920×720 / 1080 / 1200 × 深浅 × 行车/非行车，共 **12 组**布局通过。注入一个超宽子元素验证扫描确实会拒绝溢出。720 高度只验证既定回退规则，不宣称新增设计稿或硬件验收。
- 人工看图发现旧 `?demo` 场景没有明确来源提示，已补只读「含示例数据」标识；普通页面不显示。不改消息数据、数据来源合同或服务端执行语义。
- 最终树复验：HMI 类型检查 **0 错误**、**388 passed**、Vite build 通过；保护脚本核对 `types.ts` / `handleEvent` 原文不变。原有 >500 kB bundle 提示仍在。
- 同树重跑历史阅读/D12、确认 operation ID 与期限、车控证据、**22** 个舞台场景、**24** 个设置分区、**8** 个外壳夹具。付款行车隐藏、读取失败、删除取消零请求及准确 ID/scope、开发者诊断、字体与减弱动效均通过。
- 云端 HMI Dockerfile 的实际入口是 Vite dev，因此另在本机 Vite dev 上跑 **8** 个外壳夹具与发送/取消/短 speech 烟测通过。一次独立浏览器启动遇到调试端口占用，未进入应用断言；串行重跑通过，没有绕过或放宽断言。
- 证据在 `.artifacts/hmi-visual-v2/followup-visual/`、`followup-conversation/`、`followup-stage/`、`followup-settings/`、`followup-shell/`、`followup-dev-shell-final/`。所有业务帧在浏览器内隔离；本轮没有真实车控、支付或记忆删除。

## T3 · 生产发布准备

- **dry-run 已通过，待实际部署授权**。部署候选是 T2 的 `6280cdb0ddd8291440e83a90aab693dfc9bcd522`；此后的本文件更新仅是准备记录，不改变发布候选。
- 只读 status 与 dry-run 的基线一致：`b64fa70ac27bd565102ccb151b300ea66e5d9f77`，运行 release 一致，5 个入口健康。健康检查不替代业务验收。
- 干净隔离工作树：`D:/car-agent-hmi-v2-release-wt`，detached 在候选 SHA；仅复制 `dev-stack.local`，未复制 `.env`。主工作树他人的两个未提交文件未动。
- dry-run 返回 `status=dry_run`、`blocking_changes=[]`；发布锁可用，运行项目/共享脚本/受控模型均就绪。目标与已批准基础设施摘要均为 `ef2be6118821dec9950481759318535fc512905aadaadd5bdf93b320ce2b09d4`，不需要新增基础设施、Compose、schema 或 CI/CD 批准。
- 变更范围：docs 8、hmi 95、mobile 9、test 9 个路径；无后端业务、协议、环境文件、CI/CD、数据库或部署脚本改动。Android 相关源文件包含共享提取与适配，云端发布不生成 APK。
- dry-run 可用磁盘 **34,459,168,768 字节（约 32.1 GiB）**，高于 30 GiB 构建闸，但仍在 40 GiB 预警线以下；可用内存 **5,663,436,800 字节**。没有手动清理镜像/数据或改保留策略。
- 产物：隔离树 `.artifacts/releases/6280cdb0ddd8291440e83a90aab693dfc9bcd522/`，包含 `manifest.json`、`source.tar`、`transport.tar` 和 `checksums.sha256`。source SHA-256：`83748a12eba8422149ba3ecb50fe1ea9f7fc3092887adfa1104135d4d341bc5f`；manifest 同时绑定候选与部署基线。
- 准备记录 JSON：隔离树 `.artifacts/hmi-v2-release-dry-run.json`；便于复核的副本在主树 `.artifacts/hmi-visual-v2/release-prep/`。
- **未执行 `--apply`**。取得本轮人工授权后，在该干净隔离树运行：

```powershell
python scripts/dev_stack.py target show
python scripts/dev_stack.py deploy --sha 6280cdb0ddd8291440e83a90aab693dfc9bcd522 --apply
```

- apply 会重新 preflight；基线或批准材料漂移时按工具拒绝结果重新准备，不绕过。返回 submitted 后，仍须回主工作树独立执行 `status` / `verify`，记录实际 release 与验收 artifact；不得把本地夹具结果写成生产验收。
- 回退基线已明确为 `b64fa70ac27bd565102ccb151b300ea66e5d9f77`；回退也是生产动作，未经授权不执行。真实地图底图、车型线稿与车机触控/GPU 可读性仍按原交付边界单列。
