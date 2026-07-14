# scene-orchestrator Agent

场景编排助手 v0.2——**用户造场景 + 确定性执行**。「帮我创建一个钓鱼模式：座椅放平、开外循环、氛围灯调暗」一句话生成场景存为个人资产，之后「开启钓鱼模式」随叫随到；退出时**真恢复**到激活前状态。

核心纪律（设计 D2/D9）：**LLM 只在创建期当编译器**（NL→Scene DSL，逐动作过 VAL 词表白名单 + 危险动作强制确认 + 回读 NEED_CONFIRM 后落库）；**激活/执行/修复/触发全程零 LLM**——动作走既有确定性链路（AgentResult.actions → 端侧 `_dispatch_cloud_actions` → VAL 归一/校验/安全门控），同一场景同环境每次执行结果可预期。

## 能力

| intent | 说明 | 槽位 |
|---|---|---|
| `scene.create` | 一句话创建自定义场景（编译→白名单校验→回读确认→落 PG）；支持「把刚才这些存成X模式」会话沉淀 | name, spec |
| `scene.activate` | 激活场景（用户场景遮蔽同名预置）；Ground·Solve 环境求值 + `custom_params` 本次参数覆盖（「温度26」）+ 尾缀 `scene_mode.set` 状态位 + 按动作集采车况快照 | scene, custom_params |
| `scene.deactivate` | 退出并**真恢复**：按 `SCENE_ACTIVE.solved_actions`（本次实际下发集）还原到快照值，缺键退反向默认表；含座椅等危险恢复照走确认 | scene |
| `scene.update` | 改自建场景：参数级确定性直改；动作级走编译+回读闭环；预置场景引导「复制为我的」 | scene, modification |
| `scene.delete` | 删自建场景（NEED_CONFIRM）；预置场景只从列表隐藏（disabled 遮蔽记录） | scene |
| `scene.list` | 列出场景，区分「我建的 / 内置」（scene_list 卡可点激活） | — |

## 模块结构（策略引擎，设计 D9/D10）

```
src/agent.py         六 intent 编排 + 话术；确认链依赖 engine 重入（meta.confirmed）
src/catalog.py       VAL 词表加载 + validate_action/condition（白名单）+ 反向默认表 + derive_assert
src/compiler.py      LLM 编译（scene.create/update）：prompt 携带词表摘要，LLM 说了不算、校验裁决
src/solve.py         Ground·Solve 纯函数：三态求值（sat/unsat/unknown，缺数据≠满足）+ 幂等跳过已达成
src/verify.py        Verify-Repair 后台对账：activation_id 代际护栏 + 按 on_fail 处置（诚实汇报/
                     重试建议卡/驻车补做 deferred）；fail-open 绝不假警；repair 不新增执行通道
src/state_mirror.py  NATS vehicle.state.changed 车况镜像（一条订阅喂 verify/触发/驻车补做三消费方）
src/triggers.py      询问式触发（D6 零执行权）：时间 poll + 事件边沿+节流，只发建议卡
src/store.py         PG scene_item（无 PG 内存降级）；字段与 DSL 一一同名（禁改名翻译）
scenes.yaml          预置 4 场景（builtin，随镜像发版；用户同名场景遮蔽它）
knowledge/           构建期 COPY 的 VAL 词表（orchestrator/edge/knowledge，词表唯一真相源）
```

## 端口

50069

## 环境变量（详见 `docs/conventions.md` §6「场景编排」段）

`POSTGRES_DSN`（compose 注入）/ `SCENE_VERIFY_WAIT_S=4` / `SCENE_TRIGGER_POLL_S=30` / `SCENE_TRIGGER_THROTTLE_S=1800` / `SCENE_CATALOG_DIR`（镜像内，本地按仓库相对回退）/ `LLM_MODEL_SCENE`（编译模型覆盖，留空 primary）

## 运行

```bash
# 单服务调试
AGENT_PORT=50069 python agents/scene_orchestrator/main.py

# Docker（无卷挂载，改源码必须 --build）
docker compose -f compose.yaml up -d --build scene-orchestrator-agent
```

## 测试

```bash
python -m pytest agents/scene_orchestrator/tests/ -v --import-mode=importlib   # 单测 179
python test/e2e_scene.py                                                       # 真栈 26 断言
```

## 词表纪律（D3，0.1.0 的漂移教训）

场景动作的 `command/params` 必须命中 VAL 知识库（`orchestrator/edge/knowledge/{commands,entities}.yaml`）——词表外的值会被端侧 `edge_call` **静默丢弃**（0.1.0 的 `mode: external_circulation`/`color: warm_orange` 就是这么悄悄失效的：「说了开外循环，其实没开」）。编译器白名单与预置场景契约测试（`test_builtin_scenes_are_catalog_valid`）都从**同一份构建期 COPY 的词表**取材，杜绝两处真相。

## 设计文档

- `docs/design/2026-07-14-scene-orchestrator-redesign.md`——D1-D11 决策全集 + §0.5 落地纠偏 + §0.6 评审修复留档（**当前实现的唯一权威叙述**）
- `docs/design/2026-07-14-scene-orchestrator-implementation-plan.md`——实施计划（已执行完毕）
- `docs/design/2026-06-20-standalone-agents-roadmap.md` §3.2/§8——0.1.0 历史版（已被上述重设计取代）
