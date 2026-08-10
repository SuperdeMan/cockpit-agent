# B1 执行安全停止线：封死危险动作确认旁路 + T2 流式防重跑

> **状态**：已批准待实施（源自外部评审采纳批次 B1，裁决见
> [`../reviews/2026-08-10-external-review-adoption.md`](../reviews/2026-08-10-external-review-adoption.md)）
> **交付对象**：后续实施者（人或 AI agent），可独立接手
> **关联**：`orchestrator/edge/server.py`、`orchestrator/edge/val.py`、`orchestrator/edge/edge_call.py`、
> `orchestrator/cloud/loop.py`、`test/support/intent_adversarial_runtime.py`
> **优先级**：本仓库当前最高。**B1 未完成前冻结新增业务 Agent**（评审不做清单第 4 条，已采纳）。

---

## 0. 一段话给接手者

本批修四件事，全部有 `文件:行号` 级证据：① 云端空结果降级分支（CLOUD-DEGRADED-LOCAL）会绕过
危险动作二次确认直接开后备箱/解锁车门——封口；② VAL 明知是危险动作也不拒绝（PoC 注释「直接
执行」）——把确认权威下沉到 VAL，fail-closed；③ T2 流式路径因一个变量名级 bug，部分输出后
必然 unary 重跑（话术播两遍 / action 发两遍）——修判定并钉死三场景；④ 对抗测试 L2 的 VAL
命令探针已存在但不是强制证据——接入并加反向突变探针。①②③ 是安全红线级（CLAUDE.md §5
「危险动作必须二次确认」当前实际上被两条路径破坏），④ 是让 ①② 永远不再回归的证据面。

## 1. 现状与证据（已逐行核实，2026-08-10，HEAD `ef5e28e`）

### 1.1 P0：CLOUD-DEGRADED-LOCAL 绕过危险动作确认

三段代码合起来构成完整旁路：

**(a) 正常路径有闸**——`orchestrator/edge/server.py:707-708`：

```python
# 危险动作（trunk/door_lock/油箱盖/充电口盖）不秒回，落云端走二次确认闭环
if not self._confirm_required(structured):
```

**(b) 兜底路径无闸**——`orchestrator/edge/server.py:806-828`：云端流正常结束但
`final.speech` 空且无 actions 时，`classify_structured(request.text)` 重新分类，
**不检查 `_confirm_required`** 直接 `_execute_val_observed(...)`，并构造
`require_confirm=False` 的 action 回流 HMI。

**(c) VAL 不是最终权威**——`orchestrator/edge/val.py:211-215`：

```python
# 4. 需要二次确认（返回提示，由调用方决定是否继续）
if self._need_confirm(obj):
    confirm_msg = self._pick_response("Car_general_restrictions_5")
    # PoC：直接执行；真实场景返回 (False, confirm_msg) 让上层处理
    # 这里简化为标记后继续
```

攻击/事故序列：「打开后备箱」→ 端侧发现危险上云 → 云端规划失败/Agent 异常收束/空 final →
兜底分支 → 重新分类 → 直接 VAL → **后备箱打开，无任何确认**。云端 Planner 的任何空结果
故障模式（LLM 超时、解析失败、chitchat 空回复）都会触发，不需要恶意输入。

**同族缺口（评审未提，本轮核实发现）**：`server.py:787-794` 的循环只在 `which=="final"`
时更新 `cloud_speech`——云端**流式 `speech_delta` 已流出、final.speech 恰为空**时，兜底
判定依然认为「云端无输出」，本地补执行造成双执行（对危险对象则叠加上面的旁路）。

**另一条裸调用**——`server.py:885/887`（`_dispatch_cloud_actions`，云端回流 action 下发
VAL）同样无确认检查。合法确认后的执行有两条既有通道兜着（见 §2.2），该处直接执行的危险
action 只可能来自未走确认闭环的异常路径——VAL fail-closed 后自动被挡（§2.2 分析行为影响）。

### 1.2 P1-high：T2 部分输出后 unary 重跑

`orchestrator/cloud/loop.py`：

```python
streamed = False                      # :184
...（流式循环：speech → did_speak=True 并 yield；action → yield；final → final_sr）
if final_sr is not None:
    streamed = True                   # :217  ← 唯一置 True 的位置
    ...
elif streamed:                        # :255  ← 永不可达：final_sr 为 None 时 streamed 必为 False
    streamed = True                   # :257  （从注释意图看，这里显然想写 did_speak）
    ...
if not streamed:                      # :263
    async for step_result in self.executor.run(...)   # ← unary 完整重跑
```

后果：T2 单步 cloud agent 流式直通中，speech/action 已 yield 给用户但 final 丢失（流断、
超时、Agent 崩溃）→ `executor.run` 重跑该步 → 话术播两遍、Agent 调两遍、外部 API 调两遍；
若 action 已发出，则形成**重复副作用**。对照：T1 的 D0 路径实现正确
（`orchestrator/cloud/engine.py:415,420`——speech/action 一到就 `streamed = True`）。

### 1.3 L2 安全证据缺口

`test/support/intent_adversarial_runtime.py:157` 已有 `install_val_probe`（VAL 命令探针）、
`:118` 已有 `val_commands` 字段、`test/test_intent_adversarial_runtime.py:584` 有单测消费。
缺口：L0–L2 完整入口的判定断言主要看 state delta 与 emitted action，**probe 未作为强制
红灯源**——「VAL 真执行了危险命令但 action 事件被吞、状态被复原」的构造下，报告不会变红。

## 2. 方案

原则：**所有修复都让「危险动作未经确认不得执行」从「靠每条路径自觉」变成「VAL 一处结构性
保证」**；上游各闸保留形成纵深，但即使未来再新增一条执行路径，VAL 也兜得住。

### 2.1 封口 CLOUD-DEGRADED-LOCAL（`server.py`）

兜底分支（`server.py:806` 起）按序加三道挡板：

1. **云端已有任何输出即禁兜底**：把循环内对 `speech_delta`（及 action 类事件）的观察计入
   `cloud_had_output`；兜底条件从「final 空」收紧为「整条流零输出」。
2. **危险对象不兜底**：`local_structured` 非空时先过 `self._confirm_required(local_structured)`，
   命中则**不执行、不静默**——播报降级话术（与云端不可达同款口径：
   「网络不太好，这个操作需要确认后执行，请稍后再试」），并 `logger.warning` 留
   `CLOUD-DEGRADED-DANGER-BLOCKED` 标记供 obs 检索。
3. **非危险车控兜底保留**（该分支存在的意义：LLM 规划失败但原意是明确车控），执行走
   既有 `_execute_val_observed`，行为不变。

> 不采用评审建议的 `yield NEED_CONFIRM`：端侧确认闭环依赖云端挂起/恢复
> （`server.py:384` 注释、`edge_call.py:268-274`），此分支触发的前提恰是云端没有结果，
> 本地发 NEED_CONFIRM 无恢复通道承接，会造成「确认了也没人执行」的悬空确认。

### 2.2 确认权威下沉 VAL：`confirmed` 参数 fail-closed（`val.py` + 调用方）

复用仓库既有的 confirmed 凭据模式（`edge_call.py:268` `meta.confirmed=="true"` 放行），
把同一语义下沉到 VAL：

1. `val.execute(cmd, args=None, answer_length="short", multi=False)` 增加
   **`confirmed: bool = False`**，穿透 `_run` → `_structured_execute`。
2. `val.py:211-215` 的 PoC 注释块改为真拒绝：

   ```python
   if self._need_confirm(obj) and not confirmed:
       return False, self._pick_response("Car_general_restrictions_5")
   ```

3. 调用方逐点核对（生产共 5 处，已盘点）：
   - `edge_call.py:277`：传 `confirmed=confirmed`（该函数 `:268` 已解析 meta；上游闸保留，
     形成双检查纵深）。
   - `server.py:363`（`_execute_val_observed`）：透传新参，各调用点默认 False——所有本地
     路径（快路径 `:708`、multi 直通 `:526/:586`、兜底 `:811`）本就不该执行危险动作，
     默认 False 正确。
   - `server.py:885/887`（`_dispatch_cloud_actions`）：保持默认 False。**行为变化说明**：
     绕过确认闭环直接回流的危险 action 从「静默执行」变「拒绝并播报」；合法路径不受影响——
     已确认的执行经 edge_call（带 `_origin=edge_val` 回流，`server.py:873` 跳过二次下发），
     场景编排的危险动作在其编译/激活层已按 require_confirm 处理
     （`agents/scene_orchestrator/src/catalog.py:80` `_DANGER_OBJECTS`、
     `tests/test_catalog.py:91` 既有测试）。
   - `edge_agents_mod/vehicle.py:64`、`media.py:24`：走 legacy 字符串命令
     （`_legacy_execute` 仅 hvac/window/media，无危险对象），无需传参；在 `_legacy_execute`
     顶部加一行防御断言注释，声明该路径不承载危险对象（若未来 legacy 面扩到危险对象，
     必须先并入结构化路径）。
4. **话术区分**：`Car_general_restrictions_5` 是确认提示话术。fail-closed 返回后，调用方
   把它当普通拒绝播报，用户听到的是「这项操作需要确认」——语义正确；无需新话术 key。

> **为什么不是完整 ConfirmationGrant（nonce/expiry/consume/payload-hash）**：PoC 单进程
> 内存 VAL、服务间信任边界内，重放/过期/换命令攻击的威胁模型不成立；`confirmed` 布尔 +
> fail-closed 已关闭结构性风险（任何新旁路默认被拒）。完整 grant 协议登记为**真实 VAL
> （C++/SOME-IP）对接的前置项**，字段设计以评审原文 §根治方案为蓝本，届时另立卡。

### 2.3 T2 流式防重跑（`loop.py`）

最小 diff 修正 + 语义补强（不引入新组件，B5 再统一 D0/T2）：

1. 流式循环内新增 `did_action = False`；`kind == "action"` 时置 True。
2. `loop.py:255` 的 `elif streamed:` 改为 `elif did_speak or did_action:`——即评审推荐
   fallback 表的最小实现：`NO_OUTPUT` 才允许 unary fallback，`SPEECH_EMITTED`/
   `ACTION_EMITTED`/`FINAL_RECEIVED` 一律不允许。
3. 该分支内部区分两种收束：
   - 仅 speech 已流出：维持现有「合成空 OK 结果、避免重跑」的处置（现状代码语义）。
   - **action 已发出而 final 丢失**：结果不确定——合成的 StepResult 标记
     `data["_outcome_uncertain"]=true`，speech 置「操作指令已发出，结果暂时无法确认，
     请留意车辆状态」；不透明重试（评审 `UNKNOWN_AFTER_SIDE_EFFECT` 语义的最小落法；
     接入 Outcome Verifier 的 readback 留给 B5 统一组件时做）。
4. 修完后 `streamed` 变量语义收窄为「已拿到 final」——顺手改名 `got_final`，防止下一个
   读者再犯同样的混淆（改名只动本函数，零外部引用，已核实）。

### 2.4 L2 VAL command probe 接为强制证据 + 反向突变探针

1. `test/eval_intent_adversarial.py` 的 L0–L2 完整入口：把
   `intent_adversarial_runtime.py` 已产出的 `val_commands` / `dangerous_commands` 接入
   判定面——凡 case 未声明预期车控执行（金标无 action 组）而 `val_commands` 非空，
   或 `dangerous_commands` 非空且该 case 未声明确认闭环，一律判 fail（红灯语义：
   **「VAL 动了而报告看不见」不可能发生**）。
2. 新增一个**反向突变探针测试**（评审建议照单采纳，放
   `test/test_intent_adversarial_runtime.py`）：构造「VAL 真执行危险命令 + 吞掉 action
   事件 + 屏蔽 state delta」的桩，断言 L2 判定必红。这条测试测的是**尺子本身**——
   它红说明证据面失明，永远不许跳过。
3. 对抗语料补一族 `cloud_degraded` case（L0 可离线构造云端空结果）：
   - 危险对象（trunk/door_lock/fuel_tank_cover/charging_port 各 1）×「云端空 final」→
     预期：无 VAL 执行、播报降级确认话术；
   - 非危险对象（hvac 等 1-2 条）×「云端空 final」→ 预期：本地兜底执行成功（保护既有
     合法行为不被本批误伤）。
   按覆盖矩阵纪律（AGENTS.md §4.3）：新 case 标 `reviewed` 进覆盖面不进 gate 池，
   不动 gate 案例集、不碰 baseline 比对面。

## 3. 实施步骤（建议顺序，每步可独立提交）

| 步骤 | 内容 | 自检 |
|---|---|---|
| 1 | `val.py` confirmed fail-closed + `edge_call.py` 传参（§2.2.1-2） | `pytest orchestrator/edge` 全绿；既有确认闭环测试（NEED_CONFIRM→确认→执行）不破 |
| 2 | `server.py` 兜底分支三道挡板（§2.1）+ `_dispatch_cloud_actions` 行为核对（§2.2.3） | 新增单测：危险对象×空 final→不执行；delta 已流→不兜底；非危险×空 final→仍兜底 |
| 3 | `loop.py` T2 防重跑（§2.3） | 新增单测三场景：speech 后断流不重跑 / action 后断流不重跑且标不确定 / 零输出仍回退 unary。`pytest orchestrator/cloud` 全绿 |
| 4 | L2 probe 强制接入 + 反向突变探针 + `cloud_degraded` 语料族（§2.4） | `python test/eval_intent_adversarial.py --suite discovery --layer l0 --strict` exit 0（**直接读退出码，不接管道**） |
| 5 | 全量回归 | 根目录 `python -m pytest --import-mode=importlib`（当前基线 4601 passed / 14 skipped）+ `python test/smoke_edge.py` 13/13 |

预计新增测试 15±5 条。全程不改 proto、不改编排核心的 Agent 发现机制（铁律 4 不涉）。

## 4. 验收判据（全部满足才算 B1 关闭）

1. **结构性不变量**：源码级断言测试成立——对每个 `_need_confirm` 为真的对象，
   `val.execute(..., confirmed=False)` 必返回 `(False, ...)` 且状态无变化（参考
   `test_voiceprint_not_auth.py` 的源码级钉死风格）。
2. 危险对象 × 云端空 final / 空 delta-only final / 云端异常，三种收束下 VAL 状态零变化，
   且播报含「确认」语义话术。
3. T2 三场景测试绿；`orchestrator/` 选集与根全量基线均绿（数字较 4601 只增不减）。
4. 反向突变探针红灯验证通过（故意突变→L2 红；还原→绿）。
5. `docs/conventions.md` 补记：`val.execute` 的 `confirmed` 契约与 `CLOUD-DEGRADED-DANGER-BLOCKED`
   日志标记（供 badcase 排查检索）。
6. AGENTS.md §4.1 撤销「冻结新增业务 Agent」条目。

## 5. 风险与回退

| 风险 | 评估 | 处置 |
|---|---|---|
| VAL fail-closed 误伤某条未盘点的合法执行路径 | 生产调用点已全量盘点（5 处）；真栈行为差异集中在「绕闭环回流的危险 action」——本就是要挡的 | 若真栈 e2e 发现合法路径被拒：该路径按 edge_call 模式补 confirmed 凭据，**不回退 VAL 闸** |
| 兜底收紧后「云端空结果 + 非危险车控」可用性下降 | 挡板 3 显式保留非危险兜底，行为不变 | 覆盖测试钉住 |
| T2 改动影响 L1/L2 live 读数可比性 | 必然的——B1 合入后旧 SHA 读数不再代表当前代码 | 按 §4.3 纪律：需要引用主模型总分时重跑完整父 bundle，不挪用旧数字 |
| `streamed` 改名 `got_final` 波及外部 | 已核实仅函数内变量 | — |

## 6. 明确不做（本批边界）

- 不做九段 Execution Kernel / ExecutionEnvelope（采纳评估 §3.2，PoC 过度设计）。
- 不做完整 ConfirmationGrant 协议（§2.2 已述，登记为真实 VAL 对接前置项）。
- 不做 D0/T2 流式组件统一（B5 条件启动；本批只修 bug 不重构）。
- 不做幂等键/command_id（评审 N1 内容，随统一组件一起做才不产生第三套局部实现）。
