# 座舱 HMI Aurora Glass 重构 · 实施计划

- **状态**：落地中（实施蓝图 / Implementation Plan）。**进度（2026-06-30）：P0 设计系统地基 + P1 两栏外壳/右上下文舞台 + P2 ~20 张卡片（A-2~A-5）+ P3 对话动态六态（A-6）+ P4 设置横屏侧栏（A-7）+ P5 浅色主题（§12 契约，无需帧）+ A-4 信息卡按源重建（旗舰深调研分节手风琴/内联引用/缺口）+ A-5 右舞台数据驱动地图（POI 测距环·route 流动虚线·charging 按 at_km+SoC·行程按天）+ A-8 图标库（设计文件 page 32:190 的 39 个线性图标 → `Icon.tsx`/`icons.gen.ts`，补齐 16 个 → `icons.custom.ts`，4 态含 aiMoment 极光、6 推荐尺寸、`?icons` 验证台，emoji 全替换并推回 Figma A-8 页）均已落地+push（commit `2ad83e3`→`39e65a4`；`npm run build` 绿、`tsc` 无新增类型错、`node --test` 38/38、Edge headless 逐屏核对）。`types.ts` 数据契约未改。源获取：A-6 下载 zip；A-7/A-8 经 Figma MCP（`get_design_context`→`ReadMcpResourceTool`/curl asset），A-8 图标推回 Figma 用 `use_figma`+`createNodeFromSvg`。待做：P5 行车态变体(A-8 帧未出)、P6 Dashboard(B 未出 Figma)；「A 类数据缺口」需扩 `types.ts`（POI 快充/空闲位、搜索/新闻类别芯片、股票市值/估值）。**✅ 已重建 hmi 容器 + 真后端全栈 e2e 验证（2026-06-30）：CDP 驱 headless Edge 打真后端，天气/POI/股票/新闻/调研/赛事/充电/行程 8 卡族真数据渲染 + 过程区 + 确认条 + 地图舞台全对；并补：语音按钮换成小舟光球、剩余 21 处 emoji 全替 A-8 图标、ASR 流式识别上屏（见 `docs/design/2026-06-30-asr-streaming-design.md`）。**
- **交付对象**：按稿落地的 Claude Code（及人类协作者）
- **关联设计**：`docs/design/2026-06-28-figma-hmi-dashboard-redesign-brief.md`（交接简报）、`docs/design/2026-06-29-figma-hmi-dashboard-prompt.md`（Figma Make 提示词 + 输出物索引）
- **关联代码**：`hmi/src/**`（座舱前端，本次主战场）、`hmi/src/types.ts`（消息/卡片/设置契约，**不改字段**）、`dashboard/src/**`（可观测台，待 B 设计后做）
- **Figma 真相源**：
  - 设计文件 `oGlfQSUhriAEs4uH8sJnVe`（单页含 A-1~A-7 全部帧，每帧即一版定稿）
  - Make 文件 `IYsuxZHzG7t2PXtvHOT41N`（累积式 React 源码 + `guidelines/Guidelines.md` 设计契约 + `src/styles/theme.css` tokens）
- **红线**：本次是**视觉与前端布局重做**；不改后端/proto/`types.ts` 数据字段；车控仍只经 VAL（见 §6）。

---

## 0. 一页速读（TL;DR）

把当前**竖排单列网页**（`max-width:920px` 居中聊天流）重构为 Figma 已定稿的 **Aurora Glass · 极光液态座舱**：1920×1080 横屏、**左对话 + 右"上下文舞台"**两栏、液态玻璃材质、AI 极光签名渐变、"小舟"光球化身、~20 张证据卡重皮、对话动态六态、横屏设置侧栏。

**核心判断**：
1. **演进式改造，不推倒重来**。WS 状态机（`App.tsx handleEvent`）、消息/卡片契约（`types.ts`）、设置驱动（`data-theme/font/touch`）都**保留**，只换"皮"和"骨架布局"。设计契约（Make `Guidelines.md`）与现有 `types.ts` 没有冲突——前者是视觉、后者是数据。
2. **唯一较大的新增**是右侧"上下文舞台"（`ContextualStage` + 场景状态机），它需要新组件 + 一点新取数；其余都是 reskin。
3. **工作量分 7 阶段（P0→P6）**，P0~P4 是 Figma 已定稿部分（A-1~A-7），P5（行车态/浅色 = A-8）与 P6（Dashboard = B）**Figma 尚无定稿**，按设计契约规则实现或等设计。

**落地参考优先级**：`Guidelines.md`（契约/数值）＞ 设计文件帧截图（布局/状态）＞ Make `App.tsx`（逐组件实现/动效 keyframes，实现期按组件提取）。

---

## 1. 已读取与现状对照

### 1.1 本轮已读取（research 完成）
- **简报 + 提示词**：交接简报（产品语境/IA 骨架/组件清单/tokens 族/车规无障碍/移交 DoD/实现映射）、Make 提示词与 A-1~A-7 输出物索引。
- **现状代码**：`App.tsx`（WS 重连 + 看门狗 + 消息状态机 + 定位/POI「第N个」路由）、`types.ts`（全部消息/卡片/设置类型 + AGENT_CATALOG + 默认值）、组件清单与行数（`Cards.tsx` 743 / `SettingsPanel.tsx` 502 / `ChatView.tsx` 244 / `Composer.tsx` 126 / `StatusBar.tsx` 44 / `controls.tsx` 98）、`styles.css` 现有 token 体系（967 行）。
- **Figma 设计文件**：A-2 主屏、A-3 气象+金融卡、A-4 搜索/新闻/调研卡、A-5 出行/行程/赛事卡+地图舞台、A-6 对话动态六态、A-7 设置——逐帧截图已审。
- **Figma Make 源**：`guidelines/Guidelines.md`（Aurora Glass 设计契约 v1.0，完整 tokens/材质/光球三态/铁律/护栏）、`src/styles/theme.css`（shadcn 风 CSS 变量，深/浅双主题）。

### 1.2 Figma 可访问性现实（回应"读不同 version"）
- **设计文件**里 A-1~A-7 是**同一页内的独立帧**——每帧即对应版本的定稿快照，已全部截图，这就是各 version 的"输出物"。
- **Make 文件**经 MCP 只能读到**最新版本**的累积源码（A-1~A-7 组件已逐版叠加进同一 `App.tsx` + `Guidelines.md` + `theme.css`），**无法时光机式读取历史 V7/V8/V10…**。
- **结论**：实现期"看动效/实现"以 **Make 最新 `App.tsx` + 设计契约 §10 动效参数**为准（最新版已含全部组件）；若某个早期版本有被后续改掉、你想保留的动效，需要你在 Make 里导出那个 version 的 `App.tsx` 贴给我，单点补齐即可。

### 1.3 现状 → 目标 的结构差（gap）

| 维度 | 现状（`hmi/src`） | 目标（Aurora Glass） | 改动量 |
|---|---|---|---|
| 整体布局 | `.app` `max-width:920px` 居中**竖排单列**（StatusBar/Chat/Composer 纵向堆叠） | 1920×1080 **两栏**：左对话 40-46% + 右上下文舞台 54-60% | 大（重写外壳） |
| 右舞台 | **无** | 场景状态机（地图/媒体/车况/待机/大卡聚焦） | 大（全新） |
| 设计 token | 深空 HUD：`--accent #34e1d8`、`--surface rgba(18,27,48,.66)`、Space Grotesk | Aurora Glass：`--primary #46D6E0`、玻璃 `rgba(255,255,255,.056)+blur32`、Inter+Noto+JetBrains | 中（换 token + 玻璃配方） |
| AI 化身 | 无光球 | `AuroraOrb`「小舟」三态（idle/thinking/speaking）——记忆点 | 中（全新组件） |
| 极光渐变 | 无签名渐变 | `--aurora` 线性 + conic，**仅 5 处 AI 时刻**（§5 铁律） | 中 |
| ~20 卡片 | `Cards.tsx` 已实现全部类型 | 同字段、换玻璃皮 + 极光角标 + AQI7档 + 缺失/空三态 | 中（逐族 reskin） |
| 对话动态 | 已有 pending/streaming/process/confirm/proactive 字段 | 六态重皮 + 极光光标/思考律动/确认条琥珀/过程区四阶段 | 中 |
| 设置 | `SettingsPanel.tsx` 全屏覆盖竖排 | 横屏左侧栏导航 + 右内容 | 中 |
| 行车态/浅色 | 浅色 token 已有；无行车一等布局 | 行车一等低密度布局 + 浅色全语义色（A-8，**Figma 未定稿**） | 中 |
| Dashboard | 深空 HUD 现状 | 共用 tokens + 节点语义色（B，**Figma 未定稿**） | 中（待设计） |

---

## 2. 设计系统落地映射（P0 的依据）

### 2.1 Token 迁移表（`hmi/src/styles.css :root` 重写）
> 目标值取自 Make `theme.css` + `Guidelines.md` §3/§6/§7/§8。保留 `data-theme/data-font/data-touch` 切换机制。

| 现 token | 现值 | → 目标 token | 目标值 |
|---|---|---|---|
| `--bg` | `#060912` | `--bg` | `#06080F`（Space-950） |
| `--bg-2` | `#0a1124` | `--bg-2 / --space-800` | `#0A0E1A` / `#0F1525` |
| `--surface` | `rgba(18,27,48,.66)` | `--glass`（玻璃配方，见 2.2） | `rgba(255,255,255,.056)` + blur |
| `--accent` | `#34e1d8` | `--primary` | `#46D6E0`（非 AI 唯一高亮色） |
| `--accent-2` | `#4f8cff` | （并入极光）`--aurora-blue` | `#5B8CFF` |
| `--warn` | `#ffb454` | `--warn / --conf-mid` | `#F59E0B` |
| `--danger` | `#ff5a78` | `--danger` | `#EF4444` |
| `--stock-up` | `#ff5b55` | `--up`（A股红涨） | `#EF4444` |
| `--stock-down` | `#2fb37b` | `--down`（A股绿跌） | `#22C55E` |
| `--radius` | `18px` | `--radius` | `24px`（1.5rem，squircle） |
| `--font-ui` | Space Grotesk… | `--font-ui` | `'Inter','Noto Sans SC',…` |
| `--font-mono` | JetBrains Mono | `--font-mono` | 保持 JetBrains Mono（仪表数字铁律） |
| `--maxw` | `920px` | **删除** | 全屏 1920 网格 |
| — | — | `--aurora`（新） | `linear-gradient(135deg,#5BE9FF,#5B8CFF 33%,#9A6BFF 66%,#FF6BD6)` |
| — | — | `--aurora-conic`（新，光球用） | `conic-gradient(from 0deg,#5BE9FF,#5B8CFF,#9A6BFF,#FF6BD6,#5BE9FF)` |
| — | — | `--conf-high/mid/low`（新） | `#46D6E0 / #F59E0B / #6B7280` |
| — | — | AQI 7 档（新） | 优`#34D399` 良`#A3E635` 轻`#FCD34D` 中`#FB923C` 重`#EF4444` 严`#9333EA` 未知`#6B7280` |
| — | — | 玻璃阴影族（新） | glass-sm/md/lg + aurora-glow + teal-glow（契约 §8） |

> 浅色主题（`[data-theme='light']`）：背景 `#EDF1FA/#E4EBF7`、玻璃 `rgba(255,255,255,.76)`、文字 `rgba(10,14,26,.92/.55/.30)`、收敛辉光（契约 §12）。现有浅色 token 大体可用，按契约校准。

### 2.2 玻璃配方（CSS 类 `.glass`，深色默认）
```css
background: rgba(255,255,255,.056);
backdrop-filter: blur(32px) saturate(1.15);
border-top:1px solid rgba(255,255,255,.17); border-left:1px solid rgba(255,255,255,.13);
border-right:1px solid rgba(255,255,255,.07); border-bottom:1px solid rgba(255,255,255,.05);
border-radius:24px;
box-shadow:0 8px 40px rgba(0,0,0,.50),0 2px 12px rgba(0,0,0,.28),inset 0 1px 0 rgba(255,255,255,.13);
```
**低性能/行车降级**：blur 32→20px、不透明度 5.6%→10%、辉光 ×0.6（契约 §6/§11）。

### 2.3 P0 要产出的 primitives（新组件，落 `hmi/src/components/aurora/`）
| 组件 | 作用 | 参考 |
|---|---|---|
| `AuroraOrb` | 「小舟」光球，三态 idle/thinking/speaking | 契约 §10（旋速/呼吸/脉动/波纹 `#46D6E0` 全部给定）+ Make `App.tsx` |
| `Glass` | 液态玻璃容器（p/r/light/style） | §2.2 |
| `AuroraBorder` | AI 内容 1.5px 虹彩描边包装器 | 契约 §4 |
| `ConfBadge` | 置信度徽章（高/中/低，语义色） | 契约 §3-A |
| `CatChip` | 类别芯片（带语义色点） | A-4 截图 |
| `AQISection` | AQI 7 档色阶 + 当前档高亮 | A-3 截图 |
| keyframes 集 | 极光旋转 / 光球呼吸·脉动 / 流式虹彩光标 / "刚变"闪动 / 思考律动 / 聆听波纹 | 契约 §10 + Make 内联 keyframes |

> `CandlestickChart`（K线）现有，沿用并按红涨绿跌 token 校色。

---

## 3. 分阶段实施计划

> 每阶段独立可验收、可单独起 `npm run dev`（5173）目测对齐对应 Figma 帧。建议按 P0→P4 顺序（强依赖 P0）；P5/P6 可并行排后。

### P0 · 设计系统地基（tokens + primitives）
**改**：`hmi/src/styles.css`（重写 `:root` token + 玻璃/极光/keyframes 工具类）、新增 `hmi/src/components/aurora/*`（§2.3 primitives）、字体加载（Inter/Noto Sans SC/JetBrains Mono web 字体 + 系统兜底，离线降级）。
**不改**：任何业务组件逻辑。
**验收**：临时 sandbox 页渲染光球三态 + 玻璃卡 + 极光描边 + AQI 条；`prefers-reduced-motion` 下动效归零；深/浅主题切换正常。

### P1 · 外壳与两栏布局 + 右上下文舞台（最大块）
**改**：`App.tsx`（外壳 JSX：StatusBar 顶 / 左 `ChatView` / 右 `ContextualStage` / `Composer` 底；保留全部 WS/状态机逻辑不动）、`styles.css` `.app`（920 居中列 → 1920×1080 grid 两栏）。
**新增**：
- `components/ContextualStage.tsx` + `stage/`：场景状态机，依据"最近意图/卡片类型/车况/播放态"切换 **地图 / 媒体 / 车况 / 待机 / 大卡聚焦** 五场景（待机为默认）。PoC 阶段地图 = 玻璃占位 + POI 标点"数据驱动区"（与左侧 `poi_list` 编号联动高亮，A-5 已示范），媒体/车况/待机用现有数据 + 占位动画。
- 取数补强：右舞台车况/媒体场景需要车辆状态与播放态（现仅 Dashboard 侧有），HMI 侧补一处只读取数或先用占位（见 §4）。
**重做**：`StatusBar.tsx`（品牌 + 小舟名 + 在线/模型态 + TTS + 设置）、`Composer.tsx`（快捷指令轨 + 麦克风 + 输入 + 发送，落小舟 speaking 波纹）、欢迎态 Welcome（光球 + "我是小舟" + 引导芯片）。
**验收**：对齐 A-2 主屏；左右栏比例与滚动正确；右舞台默认待机、收到天气/POI 时切场景；窄屏堆叠说明（响应式行为）。

### P2 · 卡片族 reskin（~20 张，`Cards.tsx`）
**改**：`Cards.tsx` 的 `CardRenderer` 分发 + 各卡组件，**字段全部沿用 `types.ts`**，逐族换皮：
- **A 气象族**（招牌）：`weather`/`forecast` — telemetry 芯片、3 日轨、AQI7 档、预警 callout（琥珀）、生活指数；正常/缺字段降级/空 三态（A-3）。
- **B 金融族**：`stock_quote` — K 线 SVG（红涨绿跌）、市值/市盈率…、三态（A-3）。
- **C 信息族**（证据范式主场）：`search_result`/`news_brief`/`research_report`（+ 旧 `search_answer`/`news_digest`/`search_list` 兼容）。**`research_report` 重点**：`AuroraBorder` 描边 + "AI·深度调研"角标 + 分节（首节展开/余折叠）+ 引用编号[1..N] + 分节/整体置信度 + 未覆盖 gaps + 全局来源 + "在右屏展开"（升舱舞台，A-4）。
- **D 赛事族**：`sports_scores`（进球时间线主左客右镜像/live 高亮）/`sports_scorers`（榜首高亮）。
- **E 出行族**（与右舞台地图联动）：`poi_list`（候选编号供"第N个"）/`poi_detail`/`route_plan`/`charging_route`（时间线 + SoC）/`trip_itinerary`（按天 + 段间充电编织 + 可导航停靠点，A-5）。
- **F 容器**：`card_group` 多卡纵向堆叠节奏。
**抽象**：卡通用原语（卡头图标+标题+时效徽章 / 置信度徽章 / 来源折叠展开 / 时间线 / 空态 / 来源脚注）做成共享件，优先于逐张画。
**验收**：每张卡正常 + 空 + 缺字段三态对齐 A-3/A-4/A-5；证据范式（卡不复读气泡结论）保持。

### P3 · 对话动态六态（"灵魂"，`ChatView.tsx`）✅ 已落地（2026-06-30，照 A-6 源码重建 + `?demo=states` 截图核对六态及过程区展开/折叠）
**改**：`ChatView.tsx` 渲染——映射现有消息字段，重皮 + 动效：
1. 用户/助手气泡（玻璃，助手左 + 光球头像）。
2. 思考中 `ThinkingDots`（律动，对应 `pending`）。
3. 流式文本 + **虹彩光标**（对应 `streaming`/`speech_delta`）。
4. 过程区 `ProcessPanel` 四阶段（理解→规划→执行[子步骤 running→done 合并]→整理；进行中展开/完成折叠"处理过程(N步)"/**行车态强制单行不可展开**；对应 `process`/`processActive`/`driving`）。
5. 确认条 `ConfirmBar`（琥珀，确认/取消；触控 ≥50px 泊车/56px 行车；车速>0 禁危险按钮、>5km/h 升级全屏拦截——A-6 设计规范；对应 `needConfirm`/`awaitConfirm`）。
6. 主动播报气泡（💡；琥珀路况预警"更换路线/忽略" + 冷蓝任务完成 + 报告卡挂载；对应 `proactive`）。
7. 错误/超时气泡（红 X + 重试；对应 `error`/看门狗）。
**验收**：对齐 A-6 六态；三段叙事连贯（思考中→过程区→流式/最终）；确认条车规可操作。

### P4 · 设置横屏侧栏（`SettingsPanel.tsx` + `controls.tsx`）✅ 已落地（2026-06-30，照 A-7 源经 Figma MCP 读取重建；八分区 + 控件库玻璃化，真实接线全保留，`?settings[=<id>]` 逐分区截图核对）
**改**：`SettingsPanel.tsx` 全屏竖排 → 横屏左侧栏导航（8 分区）+ 右内容；`controls.tsx` 通用控件（Toggle/Segmented/TextInput/Select/Field/SectionCard/幽灵·危险按钮）重皮成玻璃组件库。
**8 分区**：语音播报（音色网格 + 试听）/语音输入/显示主题/当前位置/常用地点/助手/能力开关（10 Agent）/记忆（会话列表 + 学到画像可删）。
**验收**：对齐 A-7；所有控件可交互；设置仍经 `settings.tsx` 注入 `data-*`。

### P5 · 行车态变体 + 浅色主题（= A-8，**Figma 未定稿**）
> **进度（2026-06-30）：浅色主题 ✅ 已落地**——按 §12 契约编码（无需 Figma 帧）：aurora.css 新增 `--au-fill/--au-fill-2/--au-hi` 自适应 token（深/浅反相），把 P2–P4 忠实暗色端口里 ~59 处硬编码 `rgba(255,255,255,…)`（Cards/ChatView/SettingsPanel/controls + shell.css/cards.css 背景）token 化；`?theme=light` 验证钩子。深色无回归、`node --test` 38/38。**行车态 A-8 待 Figma 出帧再做**（材质/动效/触控降级规则+脚手架已就绪，但「一等低密度布局」未钉帧 + 缺全局行车信号；泓舟拍板"等 A-8 帧"）。地图舞台在浅色下保持暗色「活的地图」底（刻意）。
**依据**：契约 §11/§12 规则（非 Figma 帧）。
- 行车态：**一等低密度布局**（右舞台地图为主、左列只留结论条单行大字、过程区单行、热区放大、低眩光、blur 降级），由 `driving` 标志门控全屏切换。
- 浅色主题：全语义色浅色版（对比 ≥4.5:1，阳光下可读）+ 大字/大触控适配关键屏。
**验收**：行车态主屏 + 一张卡；浅色主屏 + 天气卡 + 设置。
> 建议：先实现，后补 Figma 定稿对齐；或请泓舟先跑 Make A-8 提示词产出帧再做。

### P6 · Dashboard（= B-1~B-4，**Figma 未定稿**）
**依据**：交接简报 §7（非 Figma 帧）。共用 P0 tokens + **节点语义色**（端侧/云端/VAL/LLM/工具/挂起），五面板（命令栏/链路/车辆状态/车辆动态/Agent 列表）reskin，密度更高。
**验收**：与 HMI 同 tokens；"刚变"高亮、链路逐节点入场、debug 滑块 ≠ 真车控的视觉区分保持；`dashboard` 现有单测（`*.test.tsx`）不回归。
> 建议：等 B 设计定稿（或先跑 Make B 提示词）再排期；可作为独立后续任务。

---

## 4. 右上下文舞台 —— 数据来源与场景机（落地唯一较大新增）

| 场景 | 触发 | 数据来源 | PoC 策略 |
|---|---|---|---|
| 地图 | 导航/POI/充电/路线/行程意图或对应卡 | 卡片内坐标（`poi_*`/`route_*`/`charging_*`/`trip_*`） | 玻璃地图占位 + 标点数据驱动区，与左卡编号联动；真实地图 SDK 留实现期 |
| 媒体 | 媒体播放 | 播放态（**HMI 侧暂无，需补取数**） | 先占位"正在播放"骨架 |
| 车况 | 车控（空调/座椅/氛围灯…） | 车辆状态（**HMI 侧暂无，Dashboard 有**） | 先占位被调部件 + 回显用户刚下指令 |
| 待机 | 无活跃任务（默认） | 时间/天气/车辆概览 | 时间 + 氛围动画 + 光球，体现品牌气质 |
| 大卡聚焦 | `research_report`/`trip_itinerary` 升舱 | 该卡数据 | 长卡占满舞台从容阅读（泊车态） |

**场景状态机**：维护 `currentScene`，依据最近一条 `final` 的 `ui_card.type` / 意图域映射切换，无任务回落待机。媒体/车况取数若本期不接，先占位并标注 mock（诚实信号），不阻塞 P1 验收。

---

## 5. 不可触碰（契约 / 红线）
- **`types.ts` 数据字段不改**：`Msg`/各 `*Card`/`Settings`/`AGENT_CATALOG` 是 WS 契约，视觉重做不得增删字段；确需新字段走单独契约变更流程（简报 §12）。
- **WS 状态机不改**：`App.tsx handleEvent` 的 `speech_delta|process|action|final|proactive|error` 分发、看门狗、定位/POI「第N个」路由是既有正确行为，只换渲染。
- **车控只经 VAL**：HMI 只发意图、显回执；不得出现"直接拨车身开关"的错觉控件（建议指令芯片除外）。危险动作二次确认 + 行车可操作（CLAUDE.md §5 / 简报 §3.2）。
- **设计契约不重建**：复用 Make `Guidelines.md` §9 组件与 tokens；极光只用于 §5 允许的 5 处；正文/数字/语义色绝不虹彩。
- **字体只允许** Inter / Noto Sans SC / JetBrains Mono，系统兜底。

---

## 6. 风险与降级
- **backdrop-filter 性能**：车机 SoC 上大面积实时模糊 + 持续动画易掉帧 → 提供纯色面板降级版（契约 §6）、行车态自动降级、`content-visibility`/层数控制。
- **右舞台取数缺口**：媒体/车况场景 HMI 侧暂无数据 → 本期占位 + mock 标注，不阻塞；取数作为后续小任务。
- **地图 SDK**：PoC 用占位 + 数据驱动标点；真实 SDK 接入留实现期（简报 §12 非目标）。
- **字体加载**：web 字体离线/车机内降级到系统字体——信息不得编码在特定字形（简报 §4.3）。
- **历史 Make 版本不可读**：动效以最新 Make + 契约 §10 为准；个别想保留的旧版动效需你导出贴入。

---

## 7. 验证策略
- 每阶段 `cd hmi && npm run dev`（5173）目测对齐对应 Figma 帧（A-2…A-7）。
- `npm run build`（tsc + vite）保证类型与构建不破。
- `dashboard` 改动后跑现有 `*.test.tsx` 不回归。
- 无障碍抽检：正文对比 ≥4.5:1、触控 ≥48/60px、`prefers-reduced-motion` 动效归零、不以颜色为唯一信息载体（▲▼/高中低/ON-OFF）。
- 端到端：起后端栈，真发"今天杭州天气"/"附近充电站"/"深度调研"验证 气泡↔卡↔舞台 三者联动与证据范式。

---

## 8. 待你拍板（开始前确认）
1. **范围**：本计划聚焦 **HMI（A-1~A-7 已定稿）**；行车态/浅色（A-8）与 Dashboard（B）Figma 尚无帧——是**先按契约规则实现**，还是**等你先用 Make 提示词产出 A-8/B 帧**再做？（建议：HMI 先行，A-8/B 待帧）
2. **策略**：确认走**演进式 in-place 改造**（保留 `types.ts`/WS 状态机，逐阶段换皮）——而非另起新工程再迁移。（建议：in-place）
3. **右舞台取数**：媒体/车况场景本期**先占位 mock**，取数另排？（建议：先占位）
4. **排期粒度**：是否要我把 P0~P4 进一步拆成可勾选的逐文件任务清单（TaskCreate）并开始 P0？

> 默认按"建议"推进；如无异议，下一步从 **P0 设计系统地基**动手。
