# 车控域升级：对齐「同行者公版语音指令表 6.1」统一 schema

- **状态**：草案（架构设计，**不含全量代码实现**）
- **交付对象**：后续开发者 / Agent 按本文分阶段落地
- **关联代码**：`orchestrator/edge/val.py`、`orchestrator/edge/fast_intent.py`、`orchestrator/edge/edge_agents_mod/vehicle.py`、`proto/`（VehicleCommand）
- **关联文档**：`docs/architecture/cockpit-agent-architecture.md`（铁律：车控只经 VAL；LLM 不直连车控）
- **数据来源**：飞书《同行者公版语音指令表 6.1》（bitable，77 条意图，已离线分析）

---

## 1. 现状与问题

当前车控是 **PoC 级扁平指令**，远小于真实车机覆盖面：

- `val.py`：只认 `hvac.set/on/off`、`window.open/close`、`media.play/pause/next/prev` —— **9 条扁平 command 字符串**。
- `fast_intent.py`：纯规则，`LOCAL_INTENTS` 只 8 个，且 slot 只抽 `temp`。
- 没有"受控对象 / 操作 / 位置 / 属性 / 单位"的结构化表达，无法表达"副驾座椅通风调到 3 挡""后排车窗再打开 10%""氛围灯设为蓝色"这类真实指令。
- 没有**行车安全门控的数据来源**（哪些指令行车中禁止、哪些不支持语音）。

**问题本质**：缺一套**统一的、机器可读的车控命令契约**，导致 NLU、VAL、权限、安全门控各自为政、无法规模化扩展。

公版指令表恰好提供了这套契约。本文把它作为车控域的**单一真相源**，给出落地架构。

---

## 2. 公版指令 schema（要采纳的核心契约）

每条意图统一表达为 `domain + intent + data`：

```jsonc
// 例：副驾空调温度调到 26 度
{
  "domain": "setting",        // 顶层路由域
  "intent": "control",        // 动作语义：control | query
  "data": {
    "operate": "set",         // 操作动词
    "object": "aircon",       // 受控对象（可层级）
    "attr": "temperature",    // 属性
    "positions": ["副驾"],     // 位置（可多个）
    "value": "26",
    "unit": "degree"
  }
}
```

### 2.1 字段字典（从 77 条意图归纳）

| 字段 | 取值（观测到的全集） | 说明 |
|---|---|---|
| `domain` | `setting`（车身/空调/界面控制）、`app`（应用/系统设置开闭）、`weather`（查询） | 顶层路由；决定走端侧执行还是云侧能力 |
| `intent` | `control`、`query` | 控制 vs 查询播报 |
| `operate` | `open` `close` `set` `inc` `dec` `switch` `start` `pause` `stop` `query` | 原子操作；`inc/dec` 配 `limit:little` 表"模糊调节" |
| `object` | `seat` `window` `sunroof` `sunshade` `aircon` `ambient_light` `low_beam` `headlight` `trunk` `door_lock` `fuel_tank_cover` `charging_port` `rear_view_mirror` `steering_wheel` `wiper` `fragrance` `tire_pressure_monitoring` `dashcam` `scene_mode` `driving_mode` `power_mode` `energy_recovery` `lane_departure_assistance` `lane_assistance` `accompany_home` `volume` `page` `screen` `app` `weather` | 受控对象；**可层级**：`aircon/circulation`、`aircon/wind`、`aircon/cooling`、`aircon/heating`、`rear_view_mirror/<位置>`、`dashcam/<位置>` |
| `mode` | `heating` `airing` `massage` `lumbar_support` `internal` `external` `airy` `<除雾模式>` `<除霜模式>` `<出风模式>` `<驾驶模式>` `<车辆动力模式>` `<双色氛围灯>` `小憩` … | 对象的子模式 |
| `attr` | `temperature` `speed` `brightness` `sensitivity` `height` `color` | 被调节的属性 |
| `positions` | `["主驾"]` `["副驾"]` `["前排"]` `["后排"]` `["全车"]` `["<扩展位置>"]` … | 位置数组；需归一化（见 §4.3） |
| `value` + `unit` | `unit` ∈ `degree` `level` `percent` `second`；`value` 为数字或 `<占位符>` | 量化目标 |
| `limit` | `max` `min` `little` `<高中低挡>` | 极值/模糊/挡位 |
| `direct` | 方向（座椅/后视镜调节） | 方向类调节 |
| `tag` | 颜色（氛围灯）、声源（音量） | 标签型取值 |
| `name` | 页面/界面名（当 `object:page`/`screen`） | 引导类：开关某设置界面 |

> **注意**：指令表 `data` 列存在少量脏数据（个别单元格只有 `}` 或半截 JSON）。落地时以**字段字典**为准重建 data，而非直接信任原始单元格。

### 2.2 三类指令（多意图分类，对应 task 2）

`多意图-指令类型` 字段把每条意图分为：

- **控制类**：立即执行的车身/空调控制（绝大多数）。→ 端侧快系统优先、可离线。
- **引导类**：打开某界面/设置页、导航引导（`object:page/screen`）。→ 端侧 UI 跳转。
- **播报类**：查询并 TTS 播报（如 `weather.query`）。→ 需在线、走云侧能力。

这套分类直接服务于**多意图编排**（一句话多指令时按类型决定执行/聚合策略，详见 task 2 文档）。

### 2.3 安全与网络属性（必须进契约）

| 指令表字段 | 取值 | 映射到的机制 |
|---|---|---|
| `限制` | `行车中不允许操控` | VAL 安全态门控：行车中（speed>0 或档位非 P）拒绝或转二次确认 |
| `限制` | `不支持语音操作`（如近光灯） | 命令字典标 `voice_forbidden`，NLU 命中即拒绝并解释 |
| `网络依赖` | `离线/在线` / `在线` | 路由依据：纯在线（weather）必上云；离线可用的控制类端侧兜底 |

危险动作（开车门锁、油箱盖、充电口盖等）叠加现有铁律 `require_confirm=true`（架构 §9.1）。

---

## 3. 架构映射（不破坏现有分层与铁律）

```
用户话术
  │
  ▼
[端侧 NLU / Fast Intent]  ← 升级：输出公版 data（domain/intent/data），而非扁平 intent 名
  │  控制类&离线可用 → 本地秒回         引导/播报/在线 → 上云
  ▼                                      ▼
[VAL 执行]  ← 升级：按 (object,operate,attr,positions…) 结构化执行  [云侧 Planner]
  │   + 命令字典校验 + 安全态门控 + 能力声明（车型可用对象集）          │
  ▼                                                                  ▼
真实车控（SOME-IP/CAN，PoC 为内存模拟）                          其余 Agent
```

**保持不变的铁律**：
1. 车控只经 VAL —— NLU/LLM 只产 `data` 意图，**绝不直接碰车**。
2. 规划/执行分离 —— LLM 产意图，确定性 Executor + VAL 做权限/安全校验后执行。
3. 危险动作二次确认。

**新增的关键组件：命令字典（capability dictionary）** —— 把指令表导出为机器可读的对象能力表，作为 VAL 校验与 NLU 约束的单一真相源：

```yaml
# 设想：vehicle-abstraction/commands.yaml（或 orchestrator/edge/commands.yaml）
objects:
  aircon:
    operates: [open, close, set, inc, dec]
    attrs:    [temperature, speed]
    modes:    [internal, external, 除雾, 除霜, 制冷, 制热, 吹脚, 吹人, 吹面]
    positions: true            # 支持位置
    units:    [degree, level, percent]
    online:   offline_ok
    drive_restricted: false
  low_beam:
    operates: [open, close]
    voice_forbidden: true      # 不支持语音操作
  door_lock:
    operates: [open, close]
    positions: [基础位置]
    require_confirm: true       # 危险动作
  driving_mode:
    operates: [set, close, switch]
    modes:    [节能, 运动, 舒适, ...]
    drive_restricted: true      # 行车中不允许操控
positions_alias:                # 归一化
  主驾: front_left
  副驾: front_right
  前排: [front_left, front_right]
  后排: [rear_left, rear_right]
  全车: all
```

VAL 据此做三件事：**(a) 合法性校验**（object/operate/attr 组合是否支持）、**(b) 安全门控**（`drive_restricted`/`voice_forbidden`/`require_confirm`）、**(c) 位置归一化**。

---

## 4. proto / 接口影响

当前车控经端侧 `vehicle.py` + `val.execute(command:str, args:dict)`。升级方向：

### 4.1 VAL 接口（最小侵入）
把 `execute(command: str, args: dict)` 升级为结构化入参：
```python
def execute(self, cmd: VehicleCommand) -> ExecResult:
    # cmd = {domain, intent, data:{operate,object,attr,positions,value,unit,limit,...}}
    # 1) 命令字典校验  2) 安全态门控  3) 位置归一化  4) 下发/模拟
```
`data` 在 proto 侧用 `google.protobuf.Struct`（项目已在 `ExecuteResponse.data` 用过 Struct，风格一致），避免为每个对象定义强类型 message。

### 4.2 fast_intent 升级
- PoC：规则扩展为"动词 + 对象 + 位置 + 数值"模板，输出公版 `data`。覆盖控制类高频说法（指令表"高频说法"列是现成语料）。
- Phase 2：端侧轻量 NLU 模型替换规则；意图白名单由命令字典声明（不再硬编码 `LOCAL_INTENTS`）。

### 4.3 位置/单位归一化
`positions` 中文（主驾/副驾/前排…）→ 工程枚举（front_left/...）。集中在 VAL 入口做一次，避免散落各处。

---

## 5. 分阶段落地（建议）

| 阶段 | 内容 | 不做 |
|---|---|---|
| **P1 契约落地** | ① 指令表 → `commands.yaml`（对象字典 + 安全限制 + 位置别名）；② VAL 改结构化入参 + 字典校验 + 安全门控 + 位置归一化（仍内存模拟）；③ fast_intent 规则扩展覆盖控制类高频说法；④ 黄金用例（取指令表"高频说法"） | 不接真车；不做端侧模型 |
| **P2 NLU 与多意图** | ① 端侧轻量 NLU 替换规则；② 多意图切分（task 2）；③ 引导类（开界面）+ 播报类（weather 等）接云侧 | —— |
| **P3 实车对接** | VAL 对接 SOME-IP/AUTOSAR AP/CAN；`vehicle-abstraction/` 落 C++ 实现 | —— |

---

## 6. 验收

- **P1**：`commands.yaml` 覆盖指令表全部 object；VAL 对每条"高频说法"对应的 `data` 能校验通过并返回正确话术；行车限制类在模拟"行车中"被正确门控；`voice_forbidden` 被拒绝并解释；位置归一化正确。
- 回归：`python test/smoke_edge.py` 全绿；新增车控字典契约测试。

---

## 7. 风险与取舍

- **指令表脏数据**：部分 `data` 单元格不完整 —— 以字段字典重建，勿照搬。
- **schema 膨胀**：用 `Struct` 承载 `data`，避免 proto 爆炸；代价是弱类型，靠命令字典校验兜回。
- **车型差异**：不同车型可用对象集不同 —— 命令字典应支持按车型/manifest 裁剪（指令表已有"车型标签"列可参考）。
- **离线覆盖**：控制类多为"离线/在线"，端侧需内置字典与 NLU 才能真正离线兜底；PoC 阶段规则先行。
