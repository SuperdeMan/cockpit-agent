# HMI Visual v2 · 后续验证与发布准备

- 日期：2026-10-09。
- 起点：`a49662fa69644566de6527406601cf0d0ac6e644`，I1–I6 已合入，未部署生产。
- 用户明确要求三项都做：视觉/交互残项复查、22 个既有 TypeScript 错误清理、生产发布准备；实际部署另行授权。
- 继续保持 `types.ts` 与 `App.tsx.handleEvent` 原文不变，不加 UI 依赖，不改后端语义。共享工作树的其它改动不纳入。

## T1 · 类型清理

- 精确复现 22 项：19 处缺少 `.mjs` 声明（16 个模块）、`airQualityBadge` 声明漏项、2 处 PCM typed-array 与 `BlobPart` 不兼容。
- 按实现补齐模块 API 声明，网络帧使用 `unknown`；没有通配 `declare module`、`@ts-ignore` 或放宽 tsconfig。加入 `npm run typecheck`，共享协议文件未改。
- 声纹注册与自证把 `Int16Array` 的当前视图复制为自有缓冲再交给 Blob；采样范围、格式、乘员 ID、认证和请求路径保持原样。用实际上传函数的隔离测试验证普通/共享缓冲、带偏移的视图、原 Blob 输入与源数据不变。
- Android S2S 构造既有自引用回调补一个显式类型，避免新增声明引出循环推断；无运行时逻辑改动。共享 WebSocket 的 `wake()` 也按已有实现声明。
- HMI 严格类型检查 **0 错误**，HMI **388 passed**，Vite build 通过；Android 类型检查前后均 **0 错误**，受影响的语音/网关/播放/共享准入 **64 passed**。Jest 有退出延迟提示，最终退出码为 0；不将其掩盖为没有提示。
- 原 I6 的 22 项读数保留为历史证据，不回写旧测试结果；本批证据存 `.artifacts/hmi-visual-v2/followup-types/`。

## T2 · 视觉与交互复查

待按当前提交复核大字卡片、屏幕高度适配、确认/历史阅读、设置与舞台；若发现问题，单独修复提交。

## T3 · 生产发布准备

- 当前只读 status：运行 release `b64fa70ac27bd565102ccb151b300ea66e5d9f77`，5 个入口健康。健康检查不替代业务验收。
- 最终目标提交完成并推送后，在干净的隔离 worktree 执行 dry-run，记录精确 SHA、变更范围、容量/闸状态和回退基线。
- 未执行 `--apply`，不修改 `.env`、CI/CD、数据库或基础设施批准锚。
