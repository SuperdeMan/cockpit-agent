#!/usr/bin/env python
"""新增一个端侧车控能力的**骨架生成器**（B4 §2.2）。

## 它产的是待办清单，不是成品

生成的每一段都要人填内容：话术得写得像人说的，触发规则的质量生成不出来，等价类要对着
语料裁。**刻意不落盘**——直接打印到终端由人贴过去。理由是「生成物漂移」：一个会被生成器
覆盖的文件，人改过的内容下次重跑就没了；而这些内容恰恰全是人裁的。

## 它凭什么知道缺什么

**复用门禁的车道函数**（`test/eval_capability_integrity.py`），不另写一份「新增能力要改
哪些地方」的清单。两份清单一定会漂移，而漂移的那次正好就是漏掉的那次——除雾能力落地时
漏掉对抗覆盖，就是因为「要改的地方」只活在某个人的记忆里。

用法：
    python scripts/gen_capability_skeleton.py rear_wiper --display-name 后雨刷 \\
        --operates open,close --intents rear_wiper.open,rear_wiper.close

    python scripts/gen_capability_skeleton.py front_defogger      # 已存在的对象：只列缺口
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "orchestrator" / "edge"))


def _gate_module():
    """把门禁脚本当模块加载——**唯一入口**，不复制它的判据。"""
    path = ROOT / "test" / "eval_capability_integrity.py"
    spec = importlib.util.spec_from_file_location("_capability_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COMMANDS_TMPL = """\
  {object}:
    display_name: {display_name}
    operates:
{operates}
    attrs: []
    modes: []
    positions: false
    units: []
    online: offline_ok
    drive_restricted: false
    require_confirm: {require_confirm}      # 危险动作必须 true（CLAUDE.md §5，VAL fail-closed 据此拒绝）
    effect: {effect}                        # read|write，必须与 operates 自洽
    voice_forbidden: false
    projects: []                            # 空=不做车型裁剪
    edge_intents:                           # 端侧意图名单的**唯一声明处**（vehicle.py 由此派生）
{intents}"""

_RESPONSES_TMPL = """\
{key}:
  scene: <一句话描述这次执行，用于卡片/日志>
  status: 成功
  speech_full:
  - <完整播报，例：{display_name}已打开>
  speech_brief:
  - 好的
"""

_CHECKLIST = """\
── 还有四处是人写的，生成不了 ──────────────────────────────────────────────

1. 触发规则 `orchestrator/edge/fast_intent.py`
   规则质量生成不出来。同时把意图名加进 `LOCAL_INTENTS`（那是**路由**判定：
   这句归端侧还是上云），它与 `edge_intents`（**能力目录**）是两个不同的问题。
   漏了 LOCAL_INTENTS 的后果：能力在 catalog 里有，端侧却不接，整句上云。

2. VAL 状态模拟 `orchestrator/edge/val.py::_simulate`
   开关型对象可以直接落通用兜底（它已经会写「同一个键的两种取值」）；
   有档位/开度的对象要写专属分支，否则「验证定义」车道会红——
   通用兜底键恒为 True、永远无法被证否，执行后对账面上是个恒真的空洞。
   话术 key **不用**再改 `_build_response_key`：只要按 `<object>_<on|off|操作>_success`
   命名，约定式兜底会自动接上（这条约定就是本演练当场发现缺了才加的）。

3. 对抗覆盖 `test/eval_corpus/intent_adversarial/cases/`
   每个 active intent 要正例 2 / 硬负例 2 / 对照 1。**除雾那次就是漏在这里**，
   而当时 `--strict` 的 exit 2 被管道吞成了 0。别用 `cmd | tail` 读退出码。
   ⚠ 语料唯一输入数有上界（`suites.yaml` 的 `max_cases`），加之前先看还剩多少名额。

4. 等价类 `orchestrator/edge/knowledge/nlu_objects.yaml`
   要对着**语料原文**裁：语料里这个能力被标成哪个中文标签，就并到那一行。
   不按名字相似度猜（`声音` 看着像声音设置，全量看 62% 是音量）。

5. 迁移探针 `orchestrator/edge/tests/test_vehicle_intents_migration.py`
   端侧能力面增减是产品决定，那份清单是它的**显式签收点**：确认无误后同步改
   `_BEFORE_MIGRATION`。它红 ≠ 你做错了，它红是在问「这次能力面变化是有意的吗」。

── 做完跑这四条 ────────────────────────────────────────────────────────────

    python test/eval_capability_integrity.py       # 六个维度逐对象断言
    python scripts/check_intent_gate.py            # 对抗覆盖（strict 档，唯一入口）
    python test/smoke_edge.py                      # 端侧快路径
    python -m pytest orchestrator/edge/tests -q    # 含迁移探针与等价类台账守卫

── 演练结论（2026-08-11，虚构能力 rear_wiper 全流程走过一遍）──────────────

只加 commands.yaml 对象、其余全不做时，红灯是**具名**的：话术 ×2、等价类 ×1、
迁移探针 ×1、等价类台账陈旧项 ×1、L0 对抗覆盖 ×6（逐 requirement 报「has 0, need 2」）。
逐项照做后全绿。**中途任何遗漏都有具名红灯**——B4 §4 验收判据第 1 条。
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("object", help="VAL 对象名（snake_case，如 rear_wiper）")
    ap.add_argument("--display-name", default="", help="中文名（话术与描述都用它）")
    ap.add_argument("--operates", default="open,close", help="逗号分隔，如 open,close,set")
    ap.add_argument("--intents", default="",
                    help="逗号分隔的端侧意图名；留空则按 <object>.<operate> 生成候选")
    ap.add_argument("--require-confirm", action="store_true", help="危险动作（需二次确认）")
    args = ap.parse_args()

    gate = _gate_module()
    reach, _, objects = gate._reachable()
    obj = args.object
    exists = obj in objects

    display = args.display_name or (objects.get(obj, {}) or {}).get("display_name") or f"<{obj} 中文名>"
    operates = [o.strip() for o in args.operates.split(",") if o.strip()]
    intents = [i.strip() for i in args.intents.split(",") if i.strip()] or \
        [f"{obj}.{op}" for op in operates]

    print(f"=== 能力骨架：{obj}（{display}）===")
    print(f"对象在 commands.yaml 中{'已存在' if exists else '**尚不存在**'}；"
          f"当前端侧可达对象 {len(reach)} / 声明对象 {len(objects)}\n")

    if not exists:
        from capability_meta import derive_effect

        effect = derive_effect({"operates": operates})
        print("── ① 贴进 orchestrator/edge/knowledge/commands.yaml 的 objects: 下 ──")
        print(_COMMANDS_TMPL.format(
            object=obj, display_name=display,
            operates="\n".join(f"    - {o}" for o in operates),
            require_confirm="true" if args.require_confirm else "false",
            effect=effect,
            intents="\n".join(f"    - {i}" for i in intents)))
        print()

    print("── ② 贴进 orchestrator/edge/knowledge/responses.yaml ──")
    print("（key 由 VAL `_build_response_key` 决定；下面是按 open/close 的常规命名，"
          "若该对象在 `_build_response_key` 里有专属分支请以那里为准）")
    for op in operates:
        suffix = {"open": "on", "close": "off"}.get(op, op)
        print(_RESPONSES_TMPL.format(key=f"{obj}_{suffix}_success", display_name=display))

    print("── ③ 对抗覆盖骨架（每个 intent 正例 2 / 硬负例 2 / 对照 1）──")
    for intent in intents:
        print(f"  - id: cap.{obj}.{intent.split('.')[-1]}.canonical")
        print(f"    text: <一句最自然的说法，如「打开{display}」>")
        print(f"    expect_intents: [{intent}]")
        print(f"    families: [canonical]")
    print()

    print(_CHECKLIST)

    # 已存在的对象：直接把门禁现在的读数打出来，省得人再跑一次猜哪条是自己的
    if exists:
        table, _ = gate.load_exemptions()
        mine = [e for lane in ("execution", "speech", "equivalence", "verification")
                for e in _lane_errors(gate, lane, reach, objects, table) if obj in e]
        print(f"── 门禁当前对 `{obj}` 的读数：{len(mine)} 条 ──")
        for e in mine:
            print(f"    - {e}")
        if not mine:
            print("    （没有缺口）")
    return 0


def _lane_errors(gate, lane, reach, objects, table) -> list[str]:
    if lane == "execution":
        return gate.lane_execution(reach, [], objects, table)
    if lane == "speech":
        return gate.lane_speech(reach, table)
    if lane == "equivalence":
        return gate.lane_equivalence(reach, table)
    return gate.lane_verification(reach, table, objects.get)


if __name__ == "__main__":
    sys.exit(main())
