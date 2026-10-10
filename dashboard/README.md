# Observability Dashboard

独立于 HMI 的座舱 Agent 排查与演示工作台，React 18 + TypeScript + Vite。Visual v2 的设计来源、分批实施与验证记录见
[实施计划](../docs/design/2026-10-09-dashboard-visual-v2-implementation-plan.md)。本批已获授权发布；当前 release 与 status/verify 统一看 [QA 交接 §2](../docs/reviews/2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界)。新 collector 仍兼容旧数组接口。

- **轮次**：扁平列表、筛选与会话分组；trace/前缀不受默认时间窗限制。检查器聚合结局、事实、规划、LLM、日志与原始记录。
- **实况**：指令台、本句共轴链路、历史摘要、只读车况、模拟环境、Agent 六态。完成只表示请求取得最终结果；车身变化证据来自 `val.execute.changes`。
- **日志**：保留逐条事件；按服务、级别、内容搜索，支持暂停与回到最新。
- **LLM 用量**：调用方 × 模型、用量占比、错误、归属与未上报盲区。
- **收藏**：轮次列表的 badcase 预设，豁免时间窗；重放先确认，使用独立 replay 会话。

结局显示映射只在 `src/outcomeDisplay.ts`，分类由 `runtime/outcome.py` 决定；泳道只在 `src/laneOf.ts`；
车况键、Kind 与聚合显示只在 `components/vehicle-config.ts`。
`tokens.css` 是运行时视觉声明，冻结的 Figma 对账快照在 `design/visual-v2.tokens.json`。
字体与许可证自托管；图标只读复用 HMI/mobile 数据，观测台专用图标保留 Figma 原节点来源。
浏览器页签与顶栏共用 `public/brand.svg`，保持 Visual v2 品牌标识一致。

## 运行

仓库根目录先读取 `dev-stack.local`：

```powershell
python scripts/dev_stack.py target show
python scripts/dev_stack.py dashboard
```

cloud 档只启动 Vite，地址 `http://127.0.0.1:5174`，不启动本地 Compose。

独立开发可在 `dashboard/` 内运行 `npm ci`、`npm run dev`。环境变量：

- `VITE_COLLECTOR_URL`：默认 `http://localhost:8092`。
- `VITE_EDGE_GATEWAY_URL`：默认 `http://localhost:8090`。
- `VITE_COLLECTOR_TOKEN`：由统一启动器注入；也可在令牌门粘贴 `python scripts/obs_token.py` 的输出。

令牌仅保存在当前标签页的 sessionStorage／内存，不进 localStorage、URL 或日志。主题与演示尺寸可本地记住。
首次、令牌失效、服务端未配置访问、网络失败各有独立形态。

## 深链与离线预览

- `?view=turns&trace=<trace_id>`：直达检查器；`view=live|logs|llm|badcases` 切换视图。
- `?view=turns&q=<原话或trace前缀>`：搜索。
- `?theme=dark|light|system&size=workbench|presentation`：外观。
- `?fixture=components|turns|live|live-done|live-running|live-pending|live-disabled|logs|usage`：组件与页面夹具。
- `?fixture=empty|error|missing|content-off|disconnected|legacy|token-first|token-invalid|token-not-configured|token-unreachable`：边界夹具。

夹具使用确定的示例数据；REST、WS、重放与环境写入全部停留在离线实现。
真实页面的指令仍经 Edge Gateway，车控仍经 VAL，`is_confirmation` 始终为 false。
模拟环境只改车速、电量、挡位，`DEBUG_VEHICLE_CONTROL=false` 时不可写。
v2 车况整份替换、过期不补值；来源验签不提升 simulated 属性。

新只读查询支持分类、来源、分页、采集配置和用量计数。旧 collector 返回数组时不编造总数，
新增筛选仅用于已加载范围；没有来源字段就不推断来源。未知 trace 只写“没找到”，保留策略不能证明某条记录已被清理。
collector 的查询说明见 [README](../observability/collector/README.md)。

## 验证

```powershell
# dashboard/
npm test
npm run build
# 仓库根：仅离线浏览器，不发业务指令
node dashboard/visual-qa.mjs
node dashboard/visual-qa.mjs turns:1440 turns:2560 turns:1280 live:1440 live-done:1920:presentation
node dashboard/visual-qa.mjs --interactions logs:1280 logs:1440 logs:1679 logs:1680 logs:1920 logs:2560
```

浏览器证据保存在 gitignore 的 `.artifacts/dashboard-visual-v2/`，绑定本地基线和改动树；不能替代部署或真实业务验收。
可用 `DASHBOARD_VISUAL_OUT` 指定独立证据目录。日志交互验证包含长文本、JSON 展开与跨断点缩放，检查列宽稳定、无右侧空列、详情全文和窄屏 trace 入口。
完整栈 E2E 仍遵守仓库的 remote_safe／remote_mutating 边界。
