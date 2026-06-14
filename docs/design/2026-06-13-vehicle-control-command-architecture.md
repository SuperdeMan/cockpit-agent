# 车控域升级：对齐「同行者公版语音指令表 6.1」统一 schema

- **状态**：草案（架构设计，**不含全量代码实现**）
- **交付对象**：后续开发者 / Agent，按 §8 详细待办分阶段落地
- **关联代码**：`orchestrator/edge/val.py`、`orchestrator/edge/fast_intent.py`、`orchestrator/edge/edge_agents_mod/vehicle.py`、`proto/`（VehicleCommand）
- **关联文档**：`docs/architecture/cockpit-agent-architecture.md`（铁律：车控只经 VAL；LLM 不直连车控）

## 参考源（飞书多维表格，实现时从这里取全量数据）

Base《同行者公版语音指令表 6.1》：<https://c3sz000579.feishu.cn/wiki/HS1VwShCXi4JtdkQ03ncyUWznxf>　app_token `BmoybN3OnaqCLLsXygocGviknUh`

| 表 | table_id | 链接 | 本设计是否采纳 |
|---|---|---|---|
| 意图表-基线版 | `tblN5NfQff850L5O` | [↗](https://c3sz000579.feishu.cn/wiki/HS1VwShCXi4JtdkQ03ncyUWznxf?table=tblN5NfQff850L5O) | ✅ 核心：命令 schema（domain/intent/data） |
| 分类表-基线版 | `tblMPZYYAzV8YVUp` | [↗](https://c3sz000579.feishu.cn/wiki/HS1VwShCXi4JtdkQ03ncyUWznxf?table=tblMPZYYAzV8YVUp) | ✅ 能力字典/分类层级 + 项目裁剪矩阵 |
| 基线版-词库 | `tblDLspoGsO4Iu4w` | [↗](https://c3sz000579.feishu.cn/wiki/HS1VwShCXi4JtdkQ03ncyUWznxf?table=tblDLspoGsO4Iu4w) | ✅ 实体→协议标识 归一化字典 |
| 基线版执行响应-具体响应表 | `tblclodUq24mPqnk` | [↗](https://c3sz000579.feishu.cn/wiki/HS1VwShCXi4JtdkQ03ncyUWznxf?table=tblclodUq24mPqnk) | ✅ 响应/话术层（按意图×执行结果） |
| 基线版执行响应-通用兜底反馈语 | `tblTlq6fOfrr1M8H` | [↗](https://c3sz000579.feishu.cn/wiki/HS1VwShCXi4JtdkQ03ncyUWznxf?table=tblTlq6fOfrr1M8H) | ✅ 安全门控话术 |
| 领域表-基线版 | `tblqLtkBHR481W5g` | [↗](https://c3sz000579.feishu.cn/wiki/HS1VwShCXi4JtdkQ03ncyUWznxf?table=tblqLtkBHR481W5g) | 🟡 触发域（唤醒/免唤醒/场景/可见可说），次要维度 |
| TTS发音人 | `tblFaOShScEQwwFE` | [↗](https://c3sz000579.feishu.cn/wiki/HS1VwShCXi4JtdkQ03ncyUWznxf?table=tblFaOShScEQwwFE) | ⏤ 归 task 1（且用的是另一套 TTS，非 MiMo），仅参考 |
| `ldxf2YDYMVCvSOxw` | — | （原链接给出） | ❌ 仪表盘视图、非数据表，略 |

> 拉取方式：`lark-cli base +record-list --base-token BmoybN3OnaqCLLsXygocGviknUh --table-id <tid> --limit 200`（翻页直到 `has_more=false`）。

---

## 1. 现状与问题

当前车控是 **PoC 级扁平指令**，远小于真实车机覆盖面：

- `val.py`：只认 `hvac.set/on/off`、`window.open/close`、`media.play/pause/next/prev` —— **9 条扁平 command 字符串**，话术也硬编码在 `_apply` 里。
- `fast_intent.py`：纯规则，`LOCAL_INTENTS` 只 8 个，slot 只抽 `temp`。
- 没有"受控对象 / 操作 / 位置 / 属性 / 单位"的结构化表达，无法表达"副驾座椅通风调到 3 挡""后排车窗再打开 10%""氛围灯设为蓝色"这类真实指令。
- 没有**实体归一化**（主驾→front_left）、**安全门控数据源**、**结构化话术**。

**问题本质**：缺一套**统一、机器可读的车控知识库**。公版表恰好提供了——但它不止"命令 schema"一张表，而是**五层模型**（见 §2）。

---

## 2. 参考表盘点：这是一个五层模型，不是一张表

逐表核对后，公版规范实际由五层组成，本设计**全部采纳**（领域表为次要维度）：

| 层 | 来源表 | 作用 | 对应本系统组件 |
|---|---|---|---|
| **① 命令 schema** | 意图表 | 每条意图 = `domain+intent+data{operate,object,attr,positions,value,unit,limit,...}` | NLU 产出 + VAL 入参 |
| **② 能力字典/分类** | 分类表 | 一级模块→五级操作的对象/操作层级 + **各项目（DeepWay/G91/重汽/雅迅徐工/鸿泉/华宝三一/江铃…）是否支持该意图** | `commands.yaml`（VAL 校验 + 车型裁剪） |
| **③ 实体字典** | 词库 | 自然语言实体 → **协议标识 enum**（如 `<主路>`→`main_road`，主词 `主路`、别称 `主干道`；父子库 `<车道位置>`→各子项） | `entities.yaml`（归一化层） |
| **④ 响应/话术** | 具体响应表 | 每个意图 × **执行结果/场景**（成功/失败/已打开/无此位置/无此空调…）→ 多条「普通话详细 / 普通话简洁」话术 + `执行动作` | `responses.yaml`（VAL 选话术） |
| **④b 安全兜底话术** | 通用兜底反馈语 | 安全门控场景（车辆未启动 / 行车中限制开/关 / 涉及行车安全需手动）→ 话术（标识 `Car_general_restrictions_1..4`） | `responses.yaml` 的安全分支 |
| **⑤ 触发域** 🟡 | 领域表 | 指令的**触发方式**：主唤醒词 / 全局免唤醒 / 场景指令 / 可见可说——与 `data.domain` 不同，是交互维度 | 端侧 Fast Intent + 免唤醒策略（次要，P2） |

**为什么这样取舍**：
- ②③④ 把我原设计里"凭空发明"的部分（`positions_alias`、硬编码话术）换成**公版权威数据源**——实现者直接导出即用，不用猜。
- ⑤ 领域表是**另一根轴**（怎么触发），不影响命令执行，归到端侧免唤醒/快意图设计，P2 再做。
- TTS发音人、`ldxf2YDYMVCvSOxw`（仪表盘）、修订记录/功能特性/项目意图收集表等**不进本设计**——与车控命令契约无关。

---

## 3. 命令 schema（①层：意图表）

每条意图统一表达为 `domain + intent + data`：

```jsonc
// 例：副驾空调温度调到 26 度
{ "domain": "setting", "intent": "control",
  "data": { "operate": "set", "object": "aircon", "attr": "temperature",
            "positions": ["副驾"], "value": "26", "unit": "degree" } }
```

### 3.1 字段字典（从 77 条意图归纳）

| 字段 | 取值（观测到的全集） | 说明 |
|---|---|---|
| `domain` | `setting`（车身/空调/界面控制）、`app`（应用/系统设置开闭）、`weather`（查询） | 顶层路由 |
| `intent` | `control`、`query` | 控制 vs 查询播报 |
| `operate` | `open` `close` `set` `inc` `dec` `switch` `start` `pause` `stop` `query` | `inc/dec` 配 `limit:little` 表"模糊调节" |
| `object` | `seat` `window` `sunroof` `sunshade` `aircon` `ambient_light` `low_beam` `headlight` `trunk` `door_lock` `fuel_tank_cover` `charging_port` `rear_view_mirror` `steering_wheel` `wiper` `fragrance` `tire_pressure_monitoring` `dashcam` `scene_mode` `driving_mode` `power_mode` `energy_recovery` `lane_departure_assistance` `lane_assistance` `accompany_home` `volume` `page` `screen` `app` `weather` | **可层级**：`aircon/circulation`、`aircon/wind`、`aircon/cooling`、`aircon/heating`、`rear_view_mirror/<位置>`、`dashcam/<位置>` |
| `mode` | `heating` `airing` `massage` `lumbar_support` `internal` `external` `airy` `<除雾模式>` `<除霜模式>` `<出风模式>` `<驾驶模式>` `<车辆动力模式>` `<双色氛围灯>` `小憩` … | 对象的子模式 |
| `attr` | `temperature` `speed` `brightness` `sensitivity` `height` `color` | 被调节的属性 |
| `positions` | `["主驾"]` `["副驾"]` `["前排"]` `["后排"]` `["全车"]` `["<扩展位置>"]` … | 位置数组；**归一化见 ④/§4** |
| `value` + `unit` | `unit` ∈ `degree` `level` `percent` `second`；`value` 为数字或 `<占位符>` | 量化目标 |
| `limit` | `max` `min` `little` `<高中低挡>` | 极值/模糊/挡位 |
| `direct` | 方向（座椅/后视镜调节） | 方向类调节 |
| `tag` | 颜色（氛围灯）、声源（音量） | 标签型取值 |
| `name` | 页面/界面名（当 `object:page`/`screen`） | 引导类：开关某设置界面 |

> 指令表 `data` 列有少量脏数据（个别单元格只有 `}` 或半截 JSON）。落地以**字段字典 + 分类表**为准重建，勿照搬原始单元格。

### 3.2 三类指令（多意图分类，对应 task 2）
`多意图-指令类型`：**控制类**（立即执行，端侧优先/可离线）、**引导类**（开界面/导航引导，`object:page/screen`）、**播报类**（查询并 TTS，如 weather，需在线/上云）。

### 3.3 安全与网络属性
| 字段 | 取值 | 机制 |
|---|---|---|
| `限制` | `行车中不允许操控` | 安全态门控：行车中（speed>0 / 档位非 P）拒绝或转二次确认，话术取 ④b `Car_general_restrictions_2/3` |
| `限制` | `不支持语音操作`（如近光灯） | 字典标 `voice_forbidden`，命中即拒绝，话术取 ④b `Car_general_restrictions_4` |
| `网络依赖` | `离线/在线` / `在线` | 路由：纯在线（weather）必上云；离线可用控制类端侧兜底 |

危险动作（车门锁/油箱盖/充电口盖）叠加铁律 `require_confirm=true`（架构 §9.1）。

---

## 4. 知识库三件套（②③④导出物）

把三张表导出为仓库内机器可读文件，作为 VAL/NLU 的单一真相源：

```yaml
# commands.yaml ← 分类表(tblMPZYYAzV8YVUp) + 意图表的限制/网络列
objects:
  aircon:
    operates: [open, close, set, inc, dec]
    attrs:    [temperature, speed]
    modes:    [internal, external, 除雾, 除霜, 制冷, 制热, 吹脚, 吹人, 吹面]
    positions: true
    units:    [degree, level, percent]
    online:   offline_ok
    drive_restricted: false
    projects: [DeepWay, G91, 重汽航天, ...]   # 哪些项目/车型支持（分类表 per-project 列）
  low_beam:   { operates: [open, close], voice_forbidden: true }
  door_lock:  { operates: [open, close], positions: [基础位置], require_confirm: true }
  driving_mode: { operates: [set, close, switch], modes: [节能, 运动, 舒适], drive_restricted: true }

# entities.yaml ← 词库(tblDLspoGsO4Iu4w)：主词/别称 → 协议标识，父子库展开
positions:
  主驾: front_left        # 主词→协议标识
  主驾位: front_left      # 别称（词库"主词别称"列）
  副驾: front_right
  前排: [front_left, front_right]
  全车: all
lane_positions:           # <车道位置> 父库展开
  主路: main_road
  主干道: main_road       # 别称
  辅路: sub_road

# responses.yaml ← 具体响应表(tblclodUq24mPqnk) + 兜底(tblTlq6fOfrr1M8H)
open_seat_heating_1:                       # 回复语标识（响应表主键）
  scene: 目标位置座椅加热已打开
  status: 成功
  speech_full: ["当前[<扩展位置>]座椅加热已经打开了", "[<扩展位置>]座椅加热当前已打开"]
  speech_brief: ["没关呢", "开着呢", "已经打开啦"]
Car_general_restrictions_2:                # 安全兜底
  scene: 行车过程中限制开启
  speech_full: ["抱歉，行驶过程中不支持开启该功能", "为确保行车安全，不支持开启哦"]
  speech_brief: ["行车过程中暂时无法开启哦", "行车中不支持哦"]
```

VAL 执行后据 **执行结果（成功/失败/已是该态/无此位置/无此对象）+ 安全场景** 在 `responses.yaml` 选话术（详细/简洁按 HMI `answer_length`），不再硬编码。

---

## 5. 架构映射（不破坏现有分层与铁律）

```
用户话术
  │
  ▼
[端侧 NLU / Fast Intent]  ← 升级：输出公版 data；触发域(领域表)决定唤醒/免唤醒(P2)
  │  控制类&离线可用 → 本地秒回              引导/播报/在线 → 上云
  ▼                                          ▼
[VAL 执行]                                  [云侧 Planner]
  ① entities.yaml 归一化（主驾→front_left）
  ② commands.yaml 校验（object/operate/attr 合法？该车型支持？）
  ③ 安全门控（drive_restricted / voice_forbidden / require_confirm）
  ④ 下发/模拟 → 按执行结果从 responses.yaml 选话术
  ▼
真实车控（SOME-IP/CAN，PoC 内存模拟）
```

**保持不变的铁律**：车控只经 VAL；规划/执行分离（LLM 只产 `data`）；危险动作二次确认。

---

## 6. proto / 接口影响

- **VAL 接口**：`execute(command:str, args:dict)` → `execute(cmd: VehicleCommand) -> ExecResult`，`cmd = {domain, intent, data{...}}`；`data` 用 `google.protobuf.Struct`（项目已在 `ExecuteResponse.data` 用过，风格一致），避免每对象一个强类型 message。`ExecResult` 带 `speech`（VAL 选好的话术）。
- **fast_intent**：规则模板"动词+对象+位置+数值"→公版 `data`，覆盖控制类高频说法（意图表"高频说法"列是现成语料）；P2 端侧轻量 NLU 替换规则，意图白名单由 `commands.yaml` 声明（弃 `LOCAL_INTENTS` 硬编码）。

---

## 7. 验收
- **P1**：`commands.yaml` 覆盖意图表全部 object；对每条"高频说法"对应 `data` 能归一化+校验通过+返回 `responses.yaml` 正确话术；模拟"行车中"时 `drive_restricted` 被门控且话术取自兜底表；`voice_forbidden` 被拒并解释；位置归一化正确。
- 回归：`python test/smoke_edge.py` 全绿 + 新增车控知识库契约测试。

---

## 8. 详细待办（按此执行）

### P1 契约落地（端侧内存模拟，不接真车）

**T1. 导出参考数据 → 仓库 YAML（脚本放 `scripts/`）**
- [ ] `scripts/export_commands.py`：拉分类表 + 意图表（`lark-cli base +record-list`），生成 `orchestrator/edge/knowledge/commands.yaml`。映射：分类表「五级操作/四级功能/三级对象组」→ object 层级；「意图」+ per-project 列 → `projects`；意图表「限制」→ `drive_restricted`/`voice_forbidden`、「网络依赖」→ `online`、`require_confirm`（危险动作清单：door_lock/fuel_tank_cover/charging_port…）。
- [ ] `scripts/export_entities.py`：拉词库，生成 `entities.yaml`。映射：「主词」+「主词别称」→ key，「协议标识」→ value；「子词库/概念父子库」展开父库（如 `<车道位置>`→各子项）；按「词库属性=大类」分组（位置类/模式类/颜色类…）。跳过标「该行仅作分类」的占位行。
- [ ] `scripts/export_responses.py`：拉具体响应表 + 兜底表，生成 `responses.yaml`。映射：「回复语标识」→ key，「执行状态」+「执行场景」+「分支标识」→ 选择条件，「回复语 普通话详细/简洁」（换行分隔）→ `speech_full`/`speech_brief` 列表。
- [ ] 三个脚本可重跑（公版表会更新）；产物进 git，原始拉取缓存不进。

**T2. VAL 升级（`orchestrator/edge/val.py`）**
- [ ] 启动时加载 `knowledge/*.yaml`（缺失时回退当前硬编码，保证不破坏现有 smoke）。
- [ ] `execute(cmd)` 流水线：归一化（entities）→ 合法性校验（commands：对象/操作/属性/车型）→ 安全门控（读模拟 `speed_kmh`/档位 + `drive_restricted`/`voice_forbidden`/`require_confirm`）→ 模拟改状态 → 按结果选话术（responses）。
- [ ] 车型裁剪：VAL 持当前车型 id，校验 `object.projects` 是否含之。

**T3. fast_intent 升级（`orchestrator/edge/fast_intent.py`）**
- [ ] 规则模板覆盖控制类高频说法（座椅/车窗/天窗/空调/氛围灯/后备箱…），输出公版 `data`；语料取意图表「高频说法」列。
- [ ] 端侧只接控制类离线可用项；引导/播报/在线项标记为"上云"。

**T4. 测试**
- [ ] 知识库契约测试：抽样 N 条意图，"高频说法 → data → VAL 归一化+校验通过 → responses 命中正确话术"。
- [ ] 安全门控测试：`drive_restricted` 在模拟行车中被拒 + 话术取自兜底表；`voice_forbidden` 被拒。
- [ ] 归一化测试：主驾/副驾/前排/后排/全车 → 协议标识正确。
- [ ] `python test/smoke_edge.py` 保持全绿。

### P2 NLU / 多意图 / 引导播报 / 触发域
- [ ] 端侧轻量 NLU 模型替换规则（语料：意图表全部句型列 + 词库实体）。
- [ ] 多意图切分（见 task 2 多意图文档）。
- [ ] 引导类（开界面）+ 播报类（weather 等）接云侧能力。
- [ ] **触发域（领域表）**：主唤醒/全局免唤醒/场景指令/可见可说 → 端侧唤醒与免唤醒策略（呼应意图表「全时免唤醒」列）。

### P3 实车对接
- [ ] VAL 对接 SOME-IP / AUTOSAR AP / CAN；`vehicle-abstraction/` 落 C++ 实现。
- [ ] 知识库随车型差异化（commands.yaml 的 `projects` 裁剪 → 按目标车型生成子集）。

---

## 9. 风险与取舍
- **公版表会演进**：导出脚本可重跑，产物进 git 但标注来源版本（意图表名含"6.1"）。
- **脏数据**：以字段字典 + 分类表重建 data，勿照搬意图表 `data` 列。
- **schema 膨胀**：`data` 用 `Struct` 弱类型，靠 `commands.yaml` 校验兜回。
- **车型差异**：用 `projects` 列裁剪；不同项目支持集差异大（分类表已逐项目标注）。
- **话术体量**：响应表按"意图×结果×分支"展开后条目多，`responses.yaml` 可能较大——按 object 分文件或懒加载。
- **离线覆盖**：控制类多"离线/在线"，端侧需内置三件套 + NLU 才能真离线兜底；P1 规则先行。
