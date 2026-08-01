# 端侧 capability 判别化描述：双臂差分实测（M5 P3 收尾）

> 日期：2026-08-01
> 起因：`AGENTS.md` §4.0 M5 待办首条——「78 个端侧 capability 只有 2 句描述」，
> 出处是 P3a 影子第一条观测（`关闭强力前除雾` 被规划成 `accompany_home.close` 并由 VAL 执行）。
> 卡片当时的归因：**planner 看到 74 个文本等价的工具只能靠 intent 名字猜**。
> 结论：**归因不成立（对 planner 这一侧）**。判别化描述有真收益，但收益在 registry 不在 planner。

---

## 1. 一句话

把 78 条描述从泛化改成判别化是对的，**把它们渲进 planner catalog 是没有证据的**——
跨两个 provider、100 次对照、Δ=0 零翻面；而同一批描述在 **registry 语义兜底**上把
`打开空调` 的 top-1 从 `scene-orchestrator` 掰回 `edge-vehicle`。

## 2. 改动与两个消费方

描述由 `orchestrator/edge/capabilities.py::_describe` 从 VAL 知识库机械生成
（对象名取 `commands.yaml` 新增的 `display_name`，动作取 intent 名末段，限定语取中间段），
解码走 `edge_call.decode_intent`——**与 executor 真执行时是同一个解码器**，描述不会与执行漂移。

| 消费方 | 路径 | 描述进不进 |
|---|---|---|
| planner catalog | `context.py::_catalog_item` → 每次规划的 prompt | 实测后**不进**（本文 §3） |
| registry Resolve | 按 capability 粒度 embed（`store.py::_capability_text`），LLM 失败时的兜底规划 | 进（本文 §4） |

## 3. planner 侧：Δ=0，零翻面

**方法**：唯一变量是 `_catalog_item` 里 edge 分支渲不渲染 `desc`，其余（同 provider、
同装配、同语料、同温度）逐字相同。语料 25 条，两类各半：canonical（话术几乎逐字命中
intent 名）+ **口语**（intent 名给不出词法钩子，如「下车后大灯还能多亮一会儿吗」→
`accompany_home.open`、「松油门多回收点电」→ `energy_recovery.inc`）。×2 轮全对才算过。
provider 用请求级 pin（`meta.llm_provider`），不动全局 active。

| provider | A 臂（无描述，改前） | B 臂（有描述） | Δ |
|---|---|---|---|
| minimax（MiniMax-M3，当前 active） | 22/25 | 22/25 | **0** |
| deepseek（deepseek-v4-flash） | 23/25 | 23/25 | **0** |

**100 次逐例对照零翻面。** 代价侧：满栈 catalog 10213 → 11675 字符（**+1462**，+14%），
每次规划都付。

两臂共同的 miss 也说明问题——它们都不是描述问题：
- `压线的时候提醒我一下` → 两臂 4/4 都落 `reminder.create`（「提醒」域劫持）；
- `下车后大灯还能多亮一会儿吗` → 两臂 4/4 都落 `manual.query`（疑问句形态）。

**为什么 Δ=0**：intent 名本身就是判别性文本。`lane_departure_assistance.open` 与
`lane_assistance.open` 是两个不同的英文标识符，两档模型都稳定分得开（两臂各 2/2）。
「74 个文本上完全等价的工具」这个描述对 planner 不成立。

**原始 badcase 的真根因是能力面缺口，不是描述**：`关闭强力前除雾` 在能力面上**没有对应
意图**（`commands.yaml` 的 aircon 有除雾 mode，但 `VEHICLE_INTENTS` 里没有任何除雾 intent）。
加厚到 ×4 轮看得很清楚——两臂都在错误答案之间抖：

| 臂 | ×4 轮结果 |
|---|---|
| A（无描述） | `hvac.off` ×3 / `chitchat.talk` ×1 |
| B（有描述） | `accompany_home.close` ×3 / `hvac.off` ×1 |

B 臂看起来更差，但**没有正确答案可选**时这个方向不构成定性——一个说得通的解释是描述
如实告诉模型 `hvac.off` 是「关闭空调」而非除雾，于是它不再拿这个近似答案顶上。
不管哪种解释，**结论都一样：这条 badcase 要靠补能力修，不是靠补描述。**

## 4. registry 侧：真收益，且暴露了一个被弱断言盖住的缺陷

**方法**：停 edge-orchestrator（否则它每 ~10s 周期重注册会冲掉），从宿主分别用旧/新描述
`Register`，等 registry 按 `text_hash` 重新 embed 完 74+4 条，各跑一次
`test/eval_registry_resolve.py --semantic`（直连活 registry 真向量）。

| query | A 臂（泛化描述） | B 臂（判别化描述） |
|---|---|---|
| 打开空调 | **scene-orchestrator 0.517** / edge-vehicle 0.483 | **edge-vehicle 0.675** / scene-orchestrator 0.487 |
| 把音量调大一点 | **scene-orchestrator 0.447** / reminder 0.432 / edge-media 0.428（edge-vehicle 未进前三） | **edge-vehicle 0.670** / edge-media 0.465 |
| 查一下车辆说明书怎么换轮胎 | manual-rag 0.568 / edge-vehicle 0.504 | manual-rag 0.568 / edge-vehicle **0.545**（+0.041，未夺冠） |
| 找个停车场 | ✗ parking-payment 0.62 | ✗ 同（**两臂一致 ⇒ 与本改动无关**，属既有 nearby/parking 地盘问题） |
| 其余 8 条 | 逐条同分 | 逐条同分 |

**registry Resolve 是 LLM 失败时的兜底规划路径**（`planning.py::_fallback_plan` 的
「Registry 语义路由 top-1」分支）。也就是说泛化描述时代，主链一抽风，一句纯车控指令
会被兜底路由到**场景编排 Agent**。

**这个缺陷一直在测试眼皮底下**：`registry_resolve_cases.yaml` 里 `打开空调` 与
`把音量调大一点` 只有 `forbid_top1: parking-payment` 一个否定断言——top-1 是
scene-orchestrator 照样 PASS。**断言一个否定命题守不住一个肯定性质。**
本次补了肯定的那一半（`expect_top1: edge-vehicle`，`requires_embed: true` 走 `--semantic`
车道），语义层 20/22 → **21/22**（唯一 miss 仍是既有的 `找个停车场`）。

风险侧记账：edge-vehicle 在 `换轮胎` 上从 0.504 涨到 0.545（新描述「查询胎压监测」的
胎/轮胎语义）。manual-rag 仍以 0.568 top-1，**未夺冠**；这条差距（0.023）值得下次动
描述时复看。

## 5. 沉淀的判据

1. **「A 比 B 多了信息，所以 A 更好」不是证据。** 判别化描述看起来天经地义，实测在
   planner 这一侧一次都没改变结果，而成本是每次规划 +1.4k 字符。护栏已写进
   `test_catalog_budget.py::test_edge_capabilities_stay_name_only_in_catalog`——
   要推翻它请附跨 provider 双臂数据。
2. **一个改动可以同时有两个消费方，收益只在其中一个身上。** 归因写「planner 分不清」
   的时候没人问过 registry；结果真出血的是 registry。**先枚举消费方，再谈收益。**
3. **断言否定命题的护栏要审计。** 「不是 parking-payment」放行了「是 scene-orchestrator」，
   而后者才是真实伤害。同「守红线的测试自己要被审计」（2026-07-26 验收）。
4. **描述治不了缺能力。** 原始 badcase 的对象（除雾）压根不在 `VEHICLE_INTENTS` 里，
   两臂都只能在错误答案之间抖。**归因要先确认「正确答案在候选集里」。**

## 6. 复现

```bash
# planner 双臂（需真栈 + 真 provider）——脚本随本报告口径，变量只有 _catalog_item 一行
#   A 臂 = edge 分支只渲染 intent；B 臂 = 额外渲染 desc
# registry 双臂
docker stop car-agent-edge-orchestrator-1
python - <<'PY'   # 用泛化描述覆盖注册，等 registry 按 text_hash 重 embed
...  # 见本文 §4 方法段
PY
python test/eval_registry_resolve.py --semantic
docker start car-agent-edge-orchestrator-1     # 端侧周期重注册会自动恢复判别化描述
```
