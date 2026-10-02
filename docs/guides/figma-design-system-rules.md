# Figma 设计稿落地规则（三端前端设计系统）

> 给用 Figma MCP 把设计稿落到 `hmi/`、`mobile/`、`dashboard/` 的人与 Agent。本页只定**规则与指针**，
> 不复制 token 数值——数值以 §1 的声明源为准；本页与代码冲突时以代码为准，并回改本页。
> 读数基线：2026-10-02，`main` @ `21b6e4b3`。

## 0. 硬规则（先读）

1. **先认端，再翻译**。HMI = DOM + CSS 变量 `--au-*`；mobile = React Native + `Palette` / `tokens.ts`；
   Dashboard 仍是旧「深空 HUD」皮。三端共享的是判据与图标数据（`@shared/*` 白名单），**不共享 UI 组件**
   （`AGENTS.md` §1、`docs/conventions.md` §9.33）。同一张 Figma 帧在 HMI 与 mobile 各写一份实现。
2. **Figma 给的代码是参考稿，不是代码**。`get_design_context` 默认产出 React + Tailwind；Figma Make 源是
   Tailwind v4 + shadcn/Radix + lucide-react + MUI + motion。**三端都没有这些依赖，禁止为落稿引入**，
   一律翻译成本端写法（§3、§7）。
3. **不新造 token**。颜色、圆角、阴影、材质先在声明源反查（§2.4）；查不到就先改声明源（深浅两档 +
   对应测试）再使用。禁止平行色彩体系；新代码不写 `rgba(70,214,224,…)`、`#F59E0B` 这类 token 字面量。
4. **极光只用于 AI 时刻**——契约 §5 的五处：光球、聆听/思考时屏幕边缘、流式光标、AI 内容描边/角标、
   主操作。正文、数字、语义色、普通功能图标、分隔线、面板底一律不上虹彩；非 AI 的交互高亮只用交互蓝。
5. **数值用等宽**：车速/温度/价格/距离/时间戳/ms。HMI 用 `.au-num` 或 `var(--au-font-mono)`，mobile 用 `TYPE.mono`。
6. **语义色固定且不单靠颜色**：A 股红涨绿跌；置信度高/中/低 = 交互蓝/琥珀/灰；AQI 七档。
   同时给文字或形状（▲▼、「置信度高」、档位名）。
7. **字段只来自 `hmi/src/types.ts`**。设计稿出现契约里没有的字段 = 契约变更，单独走流程，不在视觉批里顺手加。
8. **UI 只投影服务端判据**：确认、风险档、截止时间来自 `need_confirm` / `confirm_policy` / 确认台账；不在 UI
   写车速阈值之类的第二份判据（Figma A-6 的「>5 km/h 全屏拦截」已按 mobile UX v2.1 §5.3.1 作废）。
   车控入口只能是「发一句话」，不得画成直接拨车身的开关。
9. **图标只走注册表** `<Icon name>`；emoji 不作图标；新图标按 §5 规格入表。
10. **落地必须对照验证**：构建 + 测试 + 夹具截图对 Figma 截图（§9），证据绑精确 SHA。

## 1. 权威声明源（改哪里）

| 声明 | 权威位置 | 备注 |
|---|---|---|
| 视觉设计契约 | Figma Make `guidelines/Guidelines.md` v1.0，存于 `docs/design/【新】座舱Agent-HMI-A-*.zip` | 七个 zip 的 `Guidelines.md` 与 `src/styles/theme.css` 字节一致，读 A-1 即可（命令见 §8.2） |
| 各版本设计源码 | 同一批 zip 的 `src/app/App.tsx`（A-1→A-7 逐版累积导出） | MCP 读 Make 文件只能拿最新版；历史版本只在 zip 里 |
| 设计稿帧 | Figma 设计文件 `oGlfQSUhriAEs4uH8sJnVe`：页 `0:1`「A-1 Design System」+ 页 `32:190`「A-8 Icon Library」 | ID 与已核实内容见 §8.4 |
| 交接简报 / 实施计划 | [`2026-06-28-figma-hmi-dashboard-redesign-brief.md`](../design/2026-06-28-figma-hmi-dashboard-redesign-brief.md) / [`2026-06-29-figma-hmi-implementation-plan.md`](../design/2026-06-29-figma-hmi-implementation-plan.md) | 状态矩阵、Figma 组织约定、P0–P6 进度 |
| HMI token | `hmi/src/aurora.css`（`--au-*`） | `styles.css` 是旧 HUD token，过渡期并存，新代码不用 |
| mobile 色板 | `mobile/src/ui/theme.ts`（`Palette` / `DARK` / `LIGHT` / `AURORA`） | |
| mobile 尺寸、节律、材质 | `mobile/src/ui/tokens.ts`（`SPACE` `RADIUS` `TYPE` `MOTION` `TARGET` `PILL` `GLASS` `scale()`） | |
| Dashboard token | `dashboard/src/styles.css` 的 `:root` | Aurora 迁移（P6）未做 |
| 图标数据 | `hmi/src/components/icons.gen.ts`（A-8 导出，勿手改）+ `icons.custom.ts`（同规格补充）；mobile 专有 `mobile/src/ui/icons.local.ts` | mobile 经 `@shared/*` 读前两份 |
| 卡片 / 消息契约 | `hmi/src/types.ts` | 两端唯一真相源 |
| mobile 交互与材质制度 | [`2026-08-29-mobile-ux-v2-presence-redesign.md`](../design/2026-08-29-mobile-ux-v2-presence-redesign.md) | §5.11 材质、§6 行车档、§10.1 光球十条不变量 |

## 2. Token

### 2.1 HMI：`--au-*` CSS 变量

定义在 `hmi/src/aurora.css` 的 `:root`（深色默认）与 `:root[data-theme='light']`：

| 族 | 变量 |
|---|---|
| 字体 | `--au-font-ui`、`--au-font-mono` |
| 底色 | `--au-bg`、`--au-bg-2`、`--au-space-800`…`--au-space-500` |
| 文字三级 | `--au-text`、`--au-text-2`、`--au-text-3` |
| 线 / 填充 / 高光 | `--au-line`、`--au-line-2`、`--au-fill`、`--au-fill-2`、`--au-hi` |
| 交互蓝 | `--au-primary`、`--au-primary-ink` |
| 极光（仅 AI 时刻） | `--au-cyan` `--au-blue` `--au-violet` `--au-magenta`、`--au-aurora`、`--au-aurora-conic` |
| 语义 | `--au-up` `--au-down`、`--au-conf-high/mid/low`、`--au-danger` `--au-warn` `--au-online`、`--au-aqi-0`…`--au-aqi-6` |
| 玻璃 | `--au-glass-bg/-blur/-tint/-bd-top/-bd-left/-bd-right/-bd-bottom/-inset/-fallback/-shadow` |
| 圆角 | `--au-r-sm/md/lg/xl/2xl/3xl` |
| 阴影 / 辉光 | `--au-shadow-sm/md/lg`、`--au-glow-aurora`、`--au-glow-teal` |

工具类与动效：`.au-glass`（含 `@supports` 纯色降级）、`.au-aurora-border`、`.au-aurora-text`、`.au-num`、
`.au-edge-glow`、`.au-cursor`、`.au-think-dots`、`.au-flash`；keyframes 统一 `au-*` 前缀，只在 `aurora.css` 定义。

主题开关在 `<html>`：`data-theme`、`data-font`、`data-touch` 由 `hmi/src/settings.tsx` 写入。
`data-drive='on'` 只有 CSS 钩子（玻璃降级），当前没有代码设置它；行车态按消息的 `driving` 字段走
（`AuroraOrb driving`、`ChatView` 的 `ProcessArea`）。

间距与字阶**没有** CSS 变量：按契约 4px 栅格（4/8/12/16/24/32/48/64）与字阶写 px。

```tsx
// 新组件只吃 --au-*，浅色主题自动跟随
<div className="au-glass" style={{ padding: 24, borderRadius: 'var(--au-r-xl)' }}>
  <span style={{ color: 'var(--au-text-2)', fontSize: 13 }}>续航</span>
  <b className="au-num" style={{ color: 'var(--au-text)' }}>412 km</b>
</div>
```

### 2.2 mobile：`Palette` + `tokens.ts`

```tsx
import { usePalette } from '@/ui/theme'
import { RADIUS, SPACE, TYPE, scale } from '@/ui/tokens'

const p = usePalette(settings) // 深 / 浅 / 跟随系统 + 大字档
<View style={{ backgroundColor: p.card, borderRadius: RADIUS.lg, padding: SPACE[3] }}>
  <Text style={{ color: p.fg1, fontSize: scale(TYPE.body, 'text', settings.fontScale) }}>续航</Text>
  <Text style={{ color: p.accent, fontFamily: TYPE.mono }}>412 km</Text>
</View>
```

- 色只从 `Palette` 取：`bg panel card line fg1 fg2 fg3 accent accentSoft amber amberSoft red green teal hi fill fill2 glass*`；
  极光四色与渐变在 `AURORA`，只用于 §0-4 的 AI 时刻。
- 「大字」的唯一入口是 `scale(size, 'text' | 'target' | 'line', fontScale)`；只拿得到 `Palette` 的卡片渲染器用 `p.font()` / `p.target()`。
- 迁移纪律（`tokens.ts` 头注）：**新组件必用；旧组件只在触碰时顺手换**，不做全仓扫荡。

**mobile 与 Figma 契约的刻意偏差（以代码为准）**：深色 `fg2/fg3`，浅色 `fg2/fg3/accent/amber` 为过 WCAG 4.5:1
（alpha 先按底色合成）而比契约值更亮或更深，由 `mobile/test/theme.test.ts` 守；字阶整体降到手机尺度
（`TYPE.display` 32，契约 Display 48）。Figma 新值过不了这组测试时保留代码值，并把结论反馈给设计。

### 2.3 Dashboard：旧 HUD token

`dashboard/src/styles.css` 自有一套：`--bg-0…2`、`--glass`、`--stroke`、`--ink/-2/-3`、`--teal` 等实色、链路节点色
`--n-edge/cloud/val/llm/tool/wait`、字体 Chakra Petch + Space Mono。Aurora 迁移（P6）**等 Figma B 帧**；出帧前改
Dashboard 只用这套变量，不混入 `--au-*`。节点 → 颜色的唯一映射在 `dashboard/src/components/spanMeta.ts`。

### 2.4 Figma → 代码映射

先反查，再落值。拿到 Figma 的 hex / rgba，先搜声明源（`aurora.css` 的 rgba 带空格，`theme.ts` 不带）：

```bash
rg -n -i "46D6E0" hmi/src/aurora.css mobile/src/ui/theme.ts
rg -n "255, ?255, ?255, ?0\.56" hmi/src/aurora.css mobile/src/ui/theme.ts
```

**按角色映射，不按数值就近**（Make 的 `theme.css` 是 shadcn 命名，和三级文字阶不一一对应）：

| 角色（契约 / Make 变量） | HMI | mobile |
|---|---|---|
| 页面底 `--background` / Space-950 | `--au-bg` | `p.bg` |
| 次底 Space-900 | `--au-bg-2` | `p.panel` |
| 正文 `--foreground` / Primary | `--au-text` | `p.fg1` |
| 次要文字 Secondary（含 `--secondary-foreground`） | `--au-text-2` | `p.fg2` |
| 弱文字 Tertiary（含 `--muted-foreground`） | `--au-text-3` | `p.fg3` |
| 分隔 `--border` | `--au-line` / `--au-line-2` | `p.line` |
| 内嵌控件底 `--muted` / `--input` | `--au-fill` / `--au-fill-2` | `p.fill` / `p.fill2` |
| 顶缘高光 | `--au-hi` | `p.hi` |
| 交互蓝 `--primary` / `--ring` | `--au-primary` | `p.accent`（软底 `p.accentSoft`） |
| Make 的 `--accent`（极光蓝） | `--au-blue`，仅 AI 时刻 | `AURORA.blue` |
| 危险 `--destructive` | `--au-danger` | `p.red` |
| 警告 / 确认琥珀 | `--au-warn` / `--au-conf-mid` | `p.amber`（软底 `p.amberSoft`） |
| 在线 / 成功 | `--au-online` | `p.green` |
| 卡片 / 玻璃 `--card` | `.au-glass` / `--au-glass-*` | `<Glass p>` / `p.glass*` |
| 圆角 `--radius` 及阶 | `--au-r-*` | `RADIUS.*` |
| `--chart-1…5` | 极光四色 + 交互蓝，只给图表与 AI 元素 | 同左 |

### 2.5 没有 token 管线

仓库里没有 Style Dictionary、Tokens Studio 或 Figma Variables 同步脚本；三端 token 都是照契约**手工逐值落**，
关键性质靠测试守（mobile `tokens.test.ts` 数值、`theme.test.ts` 对比度；HMI `src/themeStyles.test.mjs` 浅色关键面）。
设计文件有没有定义 Figma Variables **尚未核实**：`get_variable_defs` 要求先在 Figma 里选中图层，对页节点 `0:1` 直接调用会报错
（2026-10-02 实测）；第一次需要时选中 A-1 帧再调，结果回填本节。

新增 token 的顺序：契约 / 设计确认 → 声明源加深浅两档 → 补或改测试 → 使用。

## 3. 组件

### 3.1 HMI

```
hmi/src/components/
  aurora/          设计系统原语，经 index.ts 导出（"复用，禁止重建"）：
                   AuroraOrb（idle/thinking/speaking/armed/listening）· Glass · AuroraBorder ·
                   ConfBadge · CatChip · AQISection；沙盒 AuroraPreview(?aurora)、IconGallery(?icons)
  Icon.tsx         图标渲染（§5）
  controls.tsx     设置控件库：Toggle · Segmented · TextInput · GhostBtn · DangerBtn …
  Cards.tsx        全部卡片；CardRenderer 按 card.type 分发，新卡只在这里加 case
  ChatView / Composer / StatusBar / ContextualStage / SettingsPanel / ResultDetails
```

写法：**函数组件 + inline style 对象 + `--au-*`**（照 Make 源），外加少量全局语义类（`.card`、`.au-glass`、
`.au-num`、`.au-*` 外壳类）。纯逻辑（取字段、几何、格式化）放 `src/*.mjs` + `*.d.mts` 类型 + `*.test.mjs`
（`node --test`），组件只渲染；`cardMath.mjs`、`weatherCard.mjs`、`manualCard.mjs`、`merchantUi.mjs` 是范例。
要给 mobile 共享的逻辑必须是这种形态，并登记 `mobile/shared-allowlist.json`。

新卡骨架（`MyCard` 为示意；字段取自 `types.ts`；证据范式：卡给证据，不复读气泡结论）：

```tsx
// hmi/src/components/Cards.tsx
case 'my_card': return <MyCardView card={card} onAction={onAction} />

function MyCardView({ card, onAction }: { card: MyCard; onAction?: (text: string) => void }) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon name="info" size={17} color="var(--au-text)" />
        <span style={{ flex: 1, fontSize: 14.5, fontWeight: 600 }}>{card.title}</span>
        <ProvBadge prov={card._prov} />
      </div>
      <CardHR />
      {/* 卡内动作 = 合成一句自然语言交给 onAction，不是业务写接口 */}
    </div>
  )
}
```

### 3.2 mobile

```
mobile/src/ui/              aurora/（AuroraOrb 八态 idle/thinking/speaking/armed/listening/attention/looking/muted ·
                            AuroraBackground · EdgeGlow · Glass · StreamCursor · ThinkDots）· Icon · Pill · theme · tokens · layout/
mobile/src/features/cards/  CardRenderer（REGISTRY 全量卡型 + 兜底卡 + 每卡 ErrorBoundary）· parts（CardShell / KV / Chip /
                            CardButtons / CardIcon / FreshChip / ProvBadge）· infoCards / navCards / miscCards / merchantCards
```

- 新卡：在对应 `*Cards.tsx` 写组件 → `REGISTRY` 登记。`mobile/test/cards.test.ts` 从 `types.ts` 派生卡型清单逐字比对，
  漏登记即红；未知卡型渲染兜底卡，**绝不返回 null**。
- 胶囊 / chip 一律用 `ui/Pill.tsx`（视觉 `PILL` 36/44，外框撑到 `TARGET` 48/56）；按钮档视觉即外框（`TARGET`）。
- 光球十条不变量（UX v2 §10.1）：正圆不变形、七层顺序、四色固定序、波纹用交互蓝、降级脚本化……新态只许加环与节律。

```tsx
<CardShell p={p} title="充电站" right={<ProvBadge p={p} prov={card._prov} />}>
  <KV p={p} k="空闲桩" v={card.free} />
  <CardButtons p={p} buttons={card.buttons} onSend={onSend} />
</CardShell>
```

### 3.3 Dashboard

`dashboard/src/components/*`（CommandBar / TracePanel / SpanWaterfall / VehicleState / Dynamics / TurnDetailPanel / AgentList）
与 `views/*`，全局 CSS 类（`panel__tag` 式 BEM 命名）。车辆状态面板**配置驱动**：分组、标签、图标、渲染类型只改
`components/vehicle-config.ts`。debug 滑块只设环境量，视觉上必须和真实车控回显区分开。

### 3.4 组件文档 = 预览夹具

没有 Storybook。等价物是可截图的预览入口：

| 端 | 入口 | 用途 |
|---|---|---|
| HMI | `?aurora` | 设计系统沙盒：光球各态、玻璃、极光描边、AQI |
| HMI | `?icons` | 图标全量 × 4 态 × 6 尺寸 |
| HMI | `?demo[=map\|cards\|states\|info\|charge\|trip\|route\|results]` | 卡片族与对话六态夹具（`src/demo.ts`） |
| HMI | `?settings[=<分区 id>]`、`?theme=light\|dark` | 设置分区直达、主题切换 |
| mobile | `/card-gallery?only=<type>` | 卡片画廊（`features/cards/fixtures.ts`） |
| mobile | `/state-gallery` | 在场 / 光球状态画廊（Maestro `e2e/09-state-gallery.yaml`） |
| mobile | `/blur-spike`、`/native-spike` | 材质与原生能力探针（dev 取证屏，不进主导航） |

新组件、新卡**同时加夹具**，否则没法和 Figma 截图对照。

### 3.5 Code Connect

未配置：仓库没有 `figma.config.json` 或 `*.figma.tsx`；设计文件里有没有已发布组件也未核实。要启用时
（`add_code_connect_map` 不计配额），先映射：光球 → `hmi/src/components/aurora/AuroraOrb.tsx` 与
`mobile/src/ui/aurora/AuroraOrb.tsx`，玻璃 → `Glass`，置信度 → `ConfBadge`，AQI → `AQISection`，
图标 → `Icon`；两端用 `codeConnectLabel` 区分。

## 4. 框架、样式与构建

| | HMI | mobile | Dashboard |
|---|---|---|---|
| UI | React 18 + TS 5 | React 19 + React Native 0.86 + Expo SDK 57（expo-router、Reanimated 4、react-native-svg） | React 18 + TS 5 |
| 样式 | 全局 CSS + inline style；`main.tsx` 导入顺序 `styles → aurora → shell → cards` 即层叠顺序 | inline style 对象（数组合并），基本不用 `StyleSheet`；阴影用 `boxShadow` 字符串，渐变用 `experimental_backgroundImage` | 全局 CSS |
| 构建 | Vite 5；`npm run build` 不含类型检查，另跑 `npx tsc --noEmit -p tsconfig.json` | Metro + Expo CNG（`android/` 不入库，由 `app.config.ts` + `plugins/` 生成） | Vite 8，`tsc -b && vite build` |
| 测试 | `npm test` = `node --test src/*.test.mjs` | `npm run typecheck`、`npm run lint`（`--max-warnings 0`）、`npm test`（jest-expo） | `npm test`（Vitest + jsdom + Testing Library） |
| 状态 | React state + Context（settings） | zustand | React state |
| 第三方 UI 库 | 无 | 无（`expo-blur` 只在语音层与取证屏） | 无 |

## 5. 图标

注册表：`icons.gen.ts`（A-8 的 39 个）+ `icons.custom.ts`（21 个同规格补充）；mobile 再合并 `icons.local.ts`
（8 个本地）。总览看 HMI `?icons`。

规格：

- **盒**：viewBox 24×24。`icons.gen.ts` 每项带紧致 `w/h`，由 `Icon` 居中平移；`icons.custom.ts` / `icons.local.ts` 一律 `w: 24, h: 24`。
- **线**：1.8px，round cap / join，`fill="none"`。颜色只用 `currentColor`：gen 的内层已是 `stroke="currentColor"`；
  custom / local 的 `body` 不写 `stroke` / `fill` / `style`，由外层 svg 统一施加。
- **态（HMI）**：`default`（`--au-text-2`）/ `active`（`--au-primary`）/ `disabled`（`--au-text-3`）/ `aiMoment`
  （极光渐变描边，只给 AI 时刻图标）。mobile 只实现前三态，颜色由调用方从 `Palette` 传入。
- **尺寸**：16 / 20 / 24 / 28 / 36 / 48（`ICON_SIZES`），默认 20。
- **命名**：kebab-case，族前缀 `weather-*`、`voice-*`（音色人设）、`place-*`、`charging-*`。mobile 本地的 `arrowUp` 是历史例外，新名不跟。

```tsx
// HMI
import { Icon } from './Icon'
<Icon name="charging-station" size={20} state="active" />
<Icon name="research" size={16} state="aiMoment" title="AI 深度调研" />

// mobile：react-native-svg 原生缺席时 Fabric 会在挂载期崩，渲染前先探测
import { Icon, iconRuntimeAvailable } from '@/ui/Icon'
{iconRuntimeAvailable() ? <Icon name="warning" size={16} color={p.amber} /> : <Text>!</Text>}
```

新增图标：

1. 先查是否已有：HMI `?icons`，或 `rg -n "<名字>" hmi/src/components/icons.*.ts mobile/src/ui/icons.local.ts`。
2. 来自 Figma 的新图标：导出 SVG → 规范化（去掉 fill 与色值、改 `currentColor`、去外层 transform、量紧致 w/h）。
   仓库里**没有** `icons.gen.ts` 的生成脚本，它是 A-8 快照：单个新图标写进 `icons.custom.ts`，整套重导 A-8 时才整体替换 `icons.gen.ts`。
3. mobile 独有的放 `mobile/src/ui/icons.local.ts`，名字不得与共享表撞——`Icon` 的合并顺序会静默覆盖，`mobile/test/localIcons.test.ts` 会红。
4. 改了共享文件（`icons.gen.ts` / `icons.custom.ts`）要两端验证：HMI 构建 + mobile `npm test`（含 `sharedAllowlist.test.ts`）。
5. 保持设计源一致：新图标推回 Figma A-8 页（`use_figma`，需要对该文件有编辑权限），做不到就在交付说明里登记「代码有、Figma 无」。

现存 emoji：HMI 天气卡的雪 / 雾 / 霾 / 沙尘仍回落 emoji（A-8 没出这些图标）；Dashboard `vehicle-config.ts` 的 `icon` 全是 emoji（P6 未迁）。
这些是待清偿项，不是范例。

## 6. 资产

**HMI**

- `hmi/public/` 由 Vite 原样挂在站点根，引用写绝对路径（`/fonts/...`）：`favicon.svg`、`fonts/TwemojiCountryFlags.woff2`
  （国旗子集字体，`unicode-range` 限定，只给 `.au-flag`）、`vad-capture-worklet.js`。`models/`、`kws/` 是语音模型，
  gitignore，由 `scripts/fetch-voice-models.*` 取件。
- 字体走 Google Fonts（`hmi/index.html`：Inter / Noto Sans SC / JetBrains Mono，外加旧界面的 Space Grotesk），离线降级到系统字体，
  所以**信息不得依赖特定字形**。契约只允许 Inter / Noto Sans SC / JetBrains Mono。
- 卡片图片全部来自数据：商户图过 `merchantUi.mjs::merchantImageUrl()`（https 校验；桥侧另有 `image_hosts` 白名单），
  手册图是卡片里的 `data:` URI（`manualCard.mjs`，有数量上限），付款码是 `data:image/svg+xml`；远程 `<img>` 加 `loading="lazy"`。
- 没有 CDN、图片优化管线或 SVG sprite；Vite 构建给资产加 hash。容器里跑的是 Vite dev server（`hmi/Dockerfile`），
  KWS 依赖的 COOP / COEP 头写在 `vite.config.ts`，换宿主必须同样下发。

**mobile**

- `mobile/assets/images/` 只有 App 图标、自适应图标三层、启动图、favicon（PNG，由 `app.config.ts` 引用；改它们要原生重建）。
  启动页与自适应图标底色是深空底 `#06080F`。
- 不打包字体：等宽用系统 `monospace`（`TYPE.mono`），JetBrains Mono 刻意不打包；**不要为落稿引入字体包**。
- 远程 / 数据图片用 RN `Image`（`source={{ uri }}`）；`expo-image` 在依赖里但当前没有使用。
- `assets/models/*.onnx` gitignore，经 `expo-asset` 读取；`metro.config.js` 已把 onnx 加进 `assetExts`。

**Figma 资产链接**：`get_design_context` 返回的图片 / SVG 链接是临时的，**不得写进代码**。下载后按类型落地：

- 线性图标 → §5 注册表；
- 装饰性背景、氛围光 → 用 CSS / RN 渐变复刻（参考 `shell.css` 的 `.au-scene-bg`、mobile `AuroraBackground`），不用位图；
- 确需位图 → HMI 放 `public/`（或 `src/` 下 import 让 Vite 加 hash），mobile 放 `assets/images/` 并 `require`；
- 数据绘制区（地图、K 线、队徽、声波）由代码绘制，设计只给规格。

取证截图不入库（`test/hmi_cdp/shots/` 已 gitignore）；只有 README 门面图进 `docs/images/`。

## 7. 样式方法与布局

### 7.1 HMI

- 方法：全局 CSS（token + 外壳 + 卡片皮）+ 组件 inline style；不用 CSS Modules、CSS-in-JS 或 Tailwind。
  可复用视觉进 aurora 原语或 `aurora.css` 工具类；一次性布局写 inline。
- **不用类或工具类覆盖玻璃层**（契约 §13，会破坏 `backdrop-filter`）；玻璃面一律 `.au-glass` / `<Glass>`。
- 画布：横屏 1920×1080。`.au-app` 三行栅格（状态栏 / 主区 / 输入区）；`.au-main` 两栏 `minmax(430px, 43%) 1fr`
  （左对话，右上下文舞台）。
- 层级（契约 §8）：z-0 场景底 → z-5 舞台 → z-10 玻璃面板 → z-20 输入区 / 状态栏 → z-30 头部 → z-40 弹层 / 确认条 / Tooltip → z-50 光球。
- 响应式：只有旧 `styles.css` 的 `@media (max-width: 640px)`；Aurora 外壳按 1920 横屏设计，没有断点体系。
  要新增窄屏行为，先让设计稿给出规则。
- 降级：`prefers-reduced-motion` 下循环动效归零（`aurora.css` 与 `styles.css` 各一段；新的动效类要加进 `aurora.css` 那段选择器）；
  不支持 `backdrop-filter` 时 `.au-glass` / `.card` 回落 `--au-glass-fallback`。
- 触控：泊车 ≥48px，行车 ≥56px；确认与取消都要好按。

### 7.2 mobile

- 方法：inline style 对象 + `Palette` / tokens；动效只用 Reanimated，并尊重 `useReduceMotion()` 与 `core/presence/orbPolicy.ts`。
- 布局判据全在纯函数 `ui/layout/sizeClass.ts`：宽高分别分类，`layoutMode()` = single / drawer / two-pane / tabletop /
  driving-landscape，双栏阈值 720 是内容约束。`useLayout()` 只收集事实；组件里不写 `width > 600` 之类的判断。
- 材质三档（UX v2 §5.11，`tokens.GLASS`）：
  - **G0 实色**：确认、错误、隐私、行车限制、地图上的浮层，不透明 ≥0.96、不模糊；
  - **G1 磨砂**：顶栏、语音层壳、舞台抽屉、卡壳。现状 `<Glass>` 是 tint 版；真模糊只在语音层，且必须
    `BlurTargetView` + `blurTarget`，否则 `expo-blur` 在 Android 上静默回落成无模糊；
  - **G2 反应式**：只给光球、语音层把手、选中 chip。
  - 卡片不全玻璃化，不玻璃叠玻璃。
- 尺寸：按钮 = `TARGET`（48/56），胶囊 = `PILL`（36/44）且外框撑到 `TARGET`；字号不低于 11，常规最小档用 `TYPE.micro`（12）。
- 行车档只投影 Edge 下发的 `driving` 与用户手动切换（`core/presence/drivingMode.ts`），不按车速另算一份。

### 7.3 已知偏差（照抄前先看）

- HMI 的「大字 / 大触控」在 Aurora 层基本不生效：`data-font='large'` 只改根字号，而 Aurora 组件字号是 inline px；
  `--tap` / `--bar-h` 只有旧 `styles.css` 在用。要支持需单独立项，落稿时不要声称已适配。
- `Cards.tsx` 等处仍有直接写的 `rgba(70,214,224,…)`、`#34D399`、`#F59E0B` 字面量（忠实移植 Make 源留下的）。新代码不照抄，触碰时换成 token。
- `data-drive='on'` 只有 CSS、没人设置；HMI 行车「一等低密度布局」（A-8）等帧未做。
- mobile 的 `fontSize` 字面量远多于 `TYPE.*`，按「触碰时顺手换」逐步迁。

## 8. Figma MCP 工作流

### 8.1 配额

2026-10-02 `whoami`：Professional 计划、Full 座席 ⇒ 读类工具**每天最多 200 次、每分钟 15 次**；
`whoami`、`add_code_connect_map`、`create_new_file` 不计（[官方说明](https://developers.figma.com/docs/figma-mcp-server/rate-limits-access/)）。
agent 写画布（`use_figma`）**必须是 Full 座席**，且对目标文件有编辑权限；`use_figma` 目前不能导入图片、不能用自定义字体
（Google Fonts 可用），单次返回上限 20KB。Professional 每个变量集合最多 10 个模式。
计划或座席变了先跑 `whoami` 再按官方表重算。读仍要有目的：先用仓库里的离线源，一次读够（截图 + 设计上下文），
结论写进设计文档，不反复读同一节点。

### 8.2 源的优先级

1. 设计契约 `Guidelines.md`（数值与铁律）：
   `unzip -p "docs/design/【新】座舱Agent-HMI-A-1 Design System.zip" guidelines/Guidelines.md`
2. 设计文件帧截图（布局与状态）：`get_screenshot`。
3. Make 源 `App.tsx`（组件实现、动效 keyframes）：历史版本在 zip 里，最新版经 MCP 读。
4. `get_design_context` 生成的代码：只当结构参考。

冲突时：数值以契约为准；契约与代码侧测试（对比度、触控尺寸）冲突时以代码为准，并反馈设计。

### 8.3 步骤

1. **认端与目标**：读该端 README 与本页 §1，确认是新帧还是已有组件的改版。
2. **定位节点**：从 URL 取 `fileKey` 与 `node-id`（`1-2` → `1:2`）。`get_metadata` 不带 nodeId 的页列表**可能不全**
   （2026-10-02 实测只列出 `0:1`，而 `32:190` 页确实存在）；已知节点 ID 时直接取子树，整页取子树可能超长（`0:1` 约 15 万字符）。
3. **取证**：`get_screenshot`（验收基准，存 scratchpad，不进仓库）+ `get_design_context`（结构与数值）。
4. **翻译**：Tailwind 类与硬编码色 → §2.4 token；shadcn / Radix / lucide / MUI 组件 → §3 现有原语或手写；
   图片与图标 → §5、§6；动效 → 已有 `au-*` keyframes（HMI）或 Reanimated（mobile）。
5. **数据**：字段对照 `types.ts`；缺字段走契约流程。假数据只进夹具（`demo.ts` / `fixtures.ts`），不进正式主链。
6. **夹具**：加 `?demo=…` 或 card-gallery 语料，覆盖正常 / 空 / 缺字段 / 低置信 / 行车（适用时）/ 浅色。
7. **验证**：§9。

### 8.4 设计源 ID

| 名称 | ID |
|---|---|
| 设计文件 | `oGlfQSUhriAEs4uH8sJnVe` |
| A-1 设计系统页 | 页 `0:1`：单个长帧 `6:5314`「【新】座舱Agent-HMI」（1422×6243，7 个 Section） |
| A-8 图标库页 | 页 `32:190`：主帧 `32:191`；39 个 `Icon / <name>` 组件母版在 `32:198`；回推的 16 个补充图标（`vehicle`…`school`）在同页下方 |
| Make 文件（代码 + `Guidelines.md` + `theme.css`） | `IYsuxZHzG7t2PXtvHOT41N`（`get_metadata` / `get_variable_defs` 不支持 Make 文件） |
| Android Visual v3（mobile 重构，方向 B） | `1jdZ6Cwp8pEtQJJUwg6NHS`：01 Audit `1:2`、02 Foundations `1:3`、03 Components `1:4`（组件板 `7:2`）、M1 样张 `1:5`；进度见 [brief](../design/2026-10-02-android-visual-redesign-brief.md) |

未出帧：A-8 行车态、B-1…B-4 Dashboard。HMI P5 行车态与 P6 Dashboard 等帧再做。

### 8.5 写画布（`use_figma`）的坑

2026-10-02/03 建 Android Visual v3 时实测：

- **调用是原子的**：脚本抛错时整次回滚，不留半成品，修好后可以原样重跑。`setPluginData` 不可用，要跨步骤记节点就放 JS 数组。
- **绑定变量的 paint，其 `opacity` 提交后会被重置成变量自身的 alpha**。半透明效果（按下态叠层、思考三点）改用图层 `opacity`，
  或者建一个带 alpha 的变量（如 `material/sheet-blur`）。
- **克隆节点或实例后，再绑定到同一个变量，颜色会停在黑色**（`color` 不再解析）。已经绑定到目标变量就跳过；
  确实要重绑，就先绑到别的变量，再绑回来。
- **克隆一个文字带 TEXT 属性的组件、改了字，再 `combineAsVariants`，文字会被重置成属性默认值**。
  改字前先清掉该文字节点的 `componentPropertyReferences`。
- **`isExposedInstance`** 只能用于两类实例：变体集的实例，或主组件带属性的实例。而且实例必须已经放进组件里，才能设置。
- **在自动布局帧上调 `resize()`，两个方向的尺寸模式都会变成 FIXED**。需要「随内容」的方向，事后改回 `AUTO`。
- **渐隐**（滚动区上下沿、超高提示）用 alpha 蒙版矩形（`isMask` + `maskType = "ALPHA"`），不要用颜色渐变。
  颜色渐变绑不了变量，切主题会露底。
- **变量值可能是 `VARIABLE_ALIAS`**。读 RGBA 之前，先沿着别名解析到 Primitives 集合的默认模式。
- **`upload_assets`**：位图帧落地后是 4:3，要按真实比例改尺寸。SVG 会导入为可编辑矢量帧，帧名取自文件名，可直接 `createComponentFromNode`。

## 9. 验收清单

- [ ] 没有新增依赖（尤其 Tailwind / shadcn / lucide / MUI / motion / 字体包）。
- [ ] 没有 token 字面量；新 token 已进声明源（深浅两档）并有测试。
- [ ] 极光只在 AI 时刻；数字等宽；语义色同时带文字或形状。
- [ ] 深浅两主题都截了图（HMI `?theme=light`；mobile 切系统深浅色）。
- [ ] 状态齐全：思考 / 流式 / 过程区 / 待确认 / 主动 / 空 / 错误 / 离线 / 行车（适用项）。
- [ ] 减少动效时循环动效停止；玻璃降级路径仍可读。
- [ ] 触控 ≥48（行车 ≥56）；对比度 ≥4.5:1（mobile `theme.test.ts` 绿）。
- [ ] 夹具已加，截图与 Figma `get_screenshot` 并排核对。
- [ ] 命令：HMI `npm test`、`npx tsc --noEmit -p tsconfig.json`、`npm run build`；mobile `npm run typecheck`、
      `npm run lint`、`npm test`；Dashboard `npm test`、`npm run build`。改了共享文件就两端都跑。
- [ ] 真数据联调先 `python scripts/dev_stack.py target show`；cloud 档用 `python scripts/dev_stack.py hmi` / `dashboard`，
      不起本地 Compose。mobile 真机验证按 [Android 构建与设备验证指南](android-build-and-device-validation.md)。
- [ ] 证据（截图、命令输出）绑定精确 SHA；交付说明写清「代码有、Figma 无」的差异。

## 10. 维护

- 改 token 或组件制度：先改声明源与测试，再改本页对应小节。
- 本页不记流水；批次过程进对应设计文档与 `docs/agents-history.md`。
