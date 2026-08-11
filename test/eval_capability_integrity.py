"""能力完整性门禁（外部评审采纳批次 B4 §2.1）。

## 它挡的是什么

「新增一个车控能力」当前不是一个原子动作，而是「同时记得改十来个位置」。这不是猜测——
除雾能力落地（`db6c963` + `cc87056`）的 stat 就是清单：commands.yaml、responses.yaml、
nlu_objects.yaml、fast_intent.py、val.py、edge_call.py、vehicle.py、catalog 清点基线、
对抗覆盖（**第一次就漏了**）、conventions.md。本门禁把「漏一处」变成红灯。

## 联合能力清单（§1.3，必须吸收的既有判据）

**「能力从哪里声明」和「能力写在哪个文件」是两件事。** shop 域零范例事故的根因就是门禁
只读 `manifest.yaml`，而 `mcp-bridge` 的能力由 `servers.yaml` 启动期合成——同族事故发生过
两次。所以清单从**全部声明源联合**取：`agents/*/manifest.yaml` ∪ `agents/*/servers.yaml`
∪ 端侧意图集（`VEHICLE_INTENTS` / `MEDIA_INTENTS`）。`lane_sources` 专门守「三源都还在贡献」
——某一源被读空时它先红，而不是让下游检查悄悄少查一批。

## 深检查的范围：端侧车控

方案 §2.3 明确本批只收敛**端侧车控**声明面（云侧 Agent 能力已由 manifest 机制覆盖，R2.1）。
所以逐对象的六个维度只作用在「能被端侧 intent 解码到的 `commands.yaml` 对象」上。

## 六个维度

| 车道 | 断言 |
|---|---|
| `lane_sources` | 三个声明源都非空（防「门禁只读一个文件」复发） |
| `lane_execution` | 端侧 intent ↔ commands.yaml 双向一致：intent 无孤儿、对象无不可达（不可达须进台账） |
| `lane_risk` | 每个对象有**显式** `require_confirm` 与 `effect`（read / write），且 `effect` 与 operates 自洽 |
| `lane_speech` | 每条可达 intent 的 response key 存在于 responses.yaml 且不是 `generic_success` |
| `lane_equivalence` | 每个可达对象在 `nlu_objects.yaml` 有归并（或台账登记待裁定） |
| `lane_verification` | 每条可达 intent 执行不崩，且有专属状态键（走通用兜底 ⇒ Outcome Verifier 无从对账）；`effect: read` 的查询类对象由此机械豁免 |
| `lane_adversarial` | **只确认 `--strict` 矩阵入口还在被执行**，不重复实现（唯一入口仍是 B2 的门禁脚本） |

## 台账

`orchestrator/edge/knowledge/capability_exemptions.yaml`——逐对象、逐车道，禁通配符，
每条要 `reason`。**豁免的判据是「这条不该有」，不是「懒得补」**（同 `coverage_exemptions.yaml`）。
台账里出现已不存在的对象也判红：只进不出会腐烂。

用法：
    python test/eval_capability_integrity.py            # CI 跑这个（零网络、零 LLM）
    python test/eval_capability_integrity.py --list     # 只列现状，不判红（排查用）
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import yaml

# Windows GBK 控制台下 ✓/✗ 编不出会让**门禁自己崩溃**（一绿一崩取决于宿主有没有
# PYTHONIOENCODING——同 test_eval_intent_adversarial_cli 批 1 修的环境敏感一族）。
# 项目 e2e 脚本的既有惯例，B4 落地时漏了这行。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
_gen_py = _ROOT / "gen" / "python"
if _gen_py.is_dir():
    sys.path.insert(0, str(_gen_py))
sys.path.insert(0, str(_ROOT / "orchestrator" / "edge"))

_KNOWLEDGE = _ROOT / "orchestrator" / "edge" / "knowledge"
_EXEMPTIONS = _KNOWLEDGE / "capability_exemptions.yaml"
_GATE_SCRIPT = _ROOT / "scripts" / "check_intent_gate.py"

#: 台账合法的车道名。写错车道名等于悄悄没豁免到，所以它也要被校验。
LANES = ("execution", "speech", "equivalence", "verification")


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ── 联合能力清单（§1.3）──────────────────────────────────────────────────────

def declaration_sources() -> dict[str, set[str]]:
    """三个声明源各自贡献的 intent 集合。**分开返回**而不是直接并起来——
    并起来之后「某一源被读空」这件事就再也看不见了，而那正是发生过两次的事故形态。"""
    manifest: set[str] = set()
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        for cap in _yaml(Path(path)).get("capabilities") or []:
            if cap.get("intent"):
                manifest.add(str(cap["intent"]))

    synthesized: set[str] = set()
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "servers.yaml"))):
        for server in _yaml(Path(path)).get("servers") or []:
            for tool in (server or {}).get("tools") or []:
                if (tool or {}).get("intent"):
                    synthesized.add(str(tool["intent"]))

    from edge_agents_mod.media import MEDIA_INTENTS
    from edge_agents_mod.vehicle import VEHICLE_INTENTS
    return {"manifest": manifest, "servers_yaml": synthesized,
            "edge": set(VEHICLE_INTENTS) | set(MEDIA_INTENTS)}


def _reachable() -> tuple[dict[str, list[tuple[str, dict]]], list[str], dict]:
    """(对象 → [(intent, VAL data)], 孤儿 intent, commands.yaml objects)。"""
    from edge_call import decode_intent
    from val import VAL

    objects = (VAL().commands or {}).get("objects") or {}
    known = set(objects)
    reach: dict[str, list[tuple[str, dict]]] = {}
    orphans: list[str] = []
    for intent in sorted(declaration_sources()["edge"]):
        decoded = decode_intent(intent, known_objects=known)
        if decoded is None:
            orphans.append(intent)
            continue
        reach.setdefault(decoded["data"]["object"], []).append((intent, decoded["data"]))
    return reach, orphans, objects


# ── 台账 ─────────────────────────────────────────────────────────────────────

def load_exemptions() -> tuple[dict[str, set[str]], list[str]]:
    """→ ({对象: {车道…}}, 台账自身的错误)。"""
    if not _EXEMPTIONS.exists():
        return {}, [f"台账缺失：{_EXEMPTIONS.relative_to(_ROOT)}"]
    doc = _yaml(_EXEMPTIONS)
    errs: list[str] = []
    table: dict[str, set[str]] = {}
    for i, entry in enumerate(doc.get("exemptions") or []):
        where = f"exemptions[{i}]"
        obj = str((entry or {}).get("object") or "").strip()
        lanes = [str(x) for x in ((entry or {}).get("lanes") or [])]
        reason = str((entry or {}).get("reason") or "").strip()
        if not obj:
            errs.append(f"{where}: 缺 object")
            continue
        if "*" in obj:
            errs.append(f"{where}: 禁止通配符（{obj}）——豁免必须逐对象点名")
            continue
        if not lanes:
            errs.append(f"{where}({obj}): 缺 lanes；空列表不等于全量豁免")
        bad = sorted(set(lanes) - set(LANES))
        if bad:
            errs.append(f"{where}({obj}): 未知车道 {bad}，合法值 {list(LANES)}"
                        "（车道名写错=悄悄没豁免到）")
        if not reason:
            errs.append(f"{where}({obj}): 缺 reason——豁免判据是「这条不该有」不是「懒得补」")
        table.setdefault(obj, set()).update(lanes)
    return table, errs


def _exempt(table: dict[str, set[str]], obj: str, lane: str) -> bool:
    return lane in table.get(obj, set())


# ── 车道 ─────────────────────────────────────────────────────────────────────

def lane_sources() -> list[str]:
    errs = []
    for name, intents in sorted(declaration_sources().items()):
        if not intents:
            errs.append(f"声明源 `{name}` 一条能力都没读到——门禁只读一个文件正是 shop 域"
                        "零范例事故的根因，这里先红，别让下游检查悄悄少查一批")
    return errs


def lane_execution(reach, orphans, objects, table) -> list[str]:
    from edge_call import decode_intent

    errs = [f"孤儿 intent `{i}`：解不出 commands.yaml 里的对象"
            "（intent 名与知识库对象漂移了，能力不可执行）" for i in orphans]

    # `edge_intents` 声明必须落在**它自己那个对象**底下（B4-3）。
    # 这条专挡「意图名写对了、但写错了对象块」——数量看着没变、能力却挂在别人名下，
    # 后果与漏写一样是能力不可达。
    # ⚠ 动作段拼错（`trunk.opne`）**不由这条抓**：它照样解得出对象 `trunk`，只是 operate
    #   变成了未知值——那一族由「验证定义」车道抓（落通用兜底键 `trunk_opne`）。实测确认过，
    #   这里写清楚是为了别把两条的覆盖面记混：**一条断言抓什么，要以实测为准不以命名为准**。
    known = set(objects)
    for obj, d in sorted(objects.items()):
        for intent in ((d or {}).get("edge_intents") or []):
            decoded = decode_intent(str(intent), known_objects=known)
            if decoded is None:
                errs.append(f"`{obj}.edge_intents` 里的 `{intent}` 解不出任何对象（写错名字？）")
            elif decoded["data"]["object"] != obj:
                errs.append(f"`{obj}.edge_intents` 里的 `{intent}` 实际解到对象 "
                            f"`{decoded['data']['object']}`——声明挂错了对象块")

    unreachable = sorted(set(objects) - set(reach))
    for obj in unreachable:
        if not _exempt(table, obj, "execution"):
            errs.append(f"对象 `{obj}` 在 commands.yaml 有声明，却没有任何端侧 intent 指向它"
                        "——要么补 intent，要么进台账说明它为什么不该有")
    return errs


def lane_risk(objects) -> list[str]:
    """风险面：`require_confirm` 显式声明 + `effect` 显式声明且与 operates 自洽。

    `risk` **刻意不是声明字段**（派生，见 `capability_meta.risk_of` 的模块注释）：
    B1 刚把「危险与否」收敛成 `require_confirm` 这一个权威，再手写一份会漂移。
    """
    from capability_meta import EFFECTS, derive_effect

    errs = []
    for obj, d in sorted(objects.items()):
        d = d or {}
        if "require_confirm" not in d:
            errs.append(f"对象 `{obj}` 没有显式 `require_confirm`——危险与否不能靠"
                        "「缺省 False」的隐式约定，B1 已经把这个值下沉成 VAL 的执行判据")
        declared = str(d.get("effect") or "").strip().lower()
        if not declared:
            errs.append(f"对象 `{obj}` 没有显式 `effect`（read|write）")
        elif declared not in EFFECTS:
            errs.append(f"对象 `{obj}` 的 `effect` 取值非法：{declared!r}，合法值 {list(EFFECTS)}")
        elif declared != derive_effect(d):
            errs.append(f"对象 `{obj}` 声明 `effect: {declared}`，但它的 operates "
                        f"{d.get('operates')} 推出的是 `{derive_effect(d)}`"
                        "——声明与操作面不一致，改了 operates 忘了改 effect")
    return errs


def lane_speech(reach, table) -> list[str]:
    from val import VAL

    val = VAL()
    errs = []
    for obj, items in sorted(reach.items()):
        if _exempt(table, obj, "speech"):
            continue
        for intent, data in items:
            key = val._build_response_key(obj, data["operate"], data)
            if key not in (val.responses or {}):
                errs.append(f"`{intent}` 的话术 key `{key}` 不在 responses.yaml"
                            "（VAL 会把 key 本身当话术念出来）")
            elif key == "generic_success":
                errs.append(f"`{intent}` 落到通用话术 `generic_success`"
                            "——用户听不出刚才到底做了什么；补 responses 或进台账")
    return errs


def lane_equivalence(reach, table) -> list[str]:
    doc = _yaml(_KNOWLEDGE / "nlu_objects.yaml")
    mapped = {name for names in (doc.get("objects") or {}).values() for name in (names or [])}
    return [f"对象 `{obj}` 不在 nlu_objects.yaml 的任何等价类里"
            "——端侧语义 NLU 的影子比对会把它恒判 differ（agree 曾因此从未出现过）"
            for obj in sorted(reach)
            if obj not in mapped and not _exempt(table, obj, "equivalence")]


def lane_verification(reach, table, objects_of) -> list[str]:
    """每条可达 intent 都要能落到**专属**状态键。

    VAL 的兜底分支写 `state[f"{obj}_{operate}"] = True`：`open` 与 `close` 于是各写一个键、
    永不互相清除，状态自相矛盾（`lane_assistance_open` 与 `lane_assistance_close` 同时为
    True 是实测出来的）。Outcome Verifier 读到这种键只能得到垃圾。
    """
    from val import VAL

    from capability_meta import effect_of

    errs = []
    for obj, items in sorted(reach.items()):
        # 查询类对象（`effect: read`）本来就不改状态，没有可对账的状态键是它的**正确形态**。
        # 这条豁免从「手写在台账里的一行」升级成「由 effect 机械推导」——B4 §2.2 给
        # `effect` 声明的那个消费点，落在这里。
        read_only = effect_of(objects_of(obj)) == "read"
        for intent, data in items:
            # **崩溃这一条不接受豁免**：台账能豁免的是「这个能力不该有状态键」，
            # 不是「这个能力执行会抛异常」。
            crash = _simulate_crashes(obj, data)
            if crash:
                errs.append(f"`{intent}` 执行时 VAL `_simulate` 抛异常 —— {crash}")
                continue
            if read_only or _exempt(table, obj, "verification") \
                    or _has_dedicated_state_key(obj, data):
                continue
            errs.append(f"`{intent}` 落到通用兜底键 `{obj}_{data['operate']}`"
                        "——这种键恒为 True、永远无法被证否，执行后对账面上是个恒真的空洞")
    return errs


#: 探针的三种载荷。**带值/带模式那两次是必要的**——`volume.set`、`scene_mode.set` 这类
#: 分支要有 value / mode 才走，只探裸的会把它们误判成「没有实现」（第一版就误判了 17 条，
#: 其中 4 条是探针自己的问题不是被测对象的）。判据同「A/B 之前先证明两臂真的不同」：
#: **先确认自己测的那条路径真的被走到了**。
_PROBE_PAYLOADS = ({}, {"value": "1"}, {"mode": "__probe__"})


def _has_dedicated_state_key(obj: str, data: dict) -> bool:
    from val import VAL

    for extra in _PROBE_PAYLOADS:
        probe = dict(data)
        probe.update(extra)
        key, _ = VAL()._simulate(obj, probe["operate"], probe)
        if key != f"{obj}_{probe['operate']}":
            return True
    return False


def _simulate_crashes(obj: str, data: dict) -> str:
    """`_simulate` 抛异常即返回异常描述，否则空串。

    这一条不是形式检查：`steering_wheel.height.set` 不带值时 `KeyError` 会把整条执行抛出去，
    而 `edge_call._missing_required_value` 因为 `attr` 在场提前返回、压根不会拦——
    2026-08-11 本门禁第一次跑就抓到了它（同款坑 aircon 风速那处早修过，这处漏了）。
    """
    from val import VAL

    for extra in _PROBE_PAYLOADS:
        probe = dict(data)
        probe.update(extra)
        try:
            VAL()._simulate(obj, probe["operate"], probe)
        except Exception as exc:      # noqa: BLE001 - 抓的就是「任何异常」
            return f"{type(exc).__name__}: {exc}（载荷 {extra or '裸'}）"
    return ""


def lane_adversarial() -> list[str]:
    """只确认 `--strict` 矩阵入口还在被执行——**不重复实现**（§2.1 明确）。

    唯一入口是 B2 的 `scripts/check_intent_gate.py`；这里断言它仍然跑 strict 档。
    普通 `--list` 只**展示** coverage gap，`--strict` 才把它升级成阻断——同一个门禁在
    两种模式下严厉程度不同，2026-08-10 除雾漏覆盖那次就栽在这上面。
    """
    if not _GATE_SCRIPT.exists():
        return [f"对抗覆盖矩阵入口不见了：{_GATE_SCRIPT.relative_to(_ROOT)}"]
    src = _GATE_SCRIPT.read_text(encoding="utf-8")
    if "--strict" not in src:
        return ["`scripts/check_intent_gate.py` 里找不到 `--strict`——"
                "对抗覆盖降级成了只展示不阻断"]
    return []


# ── 主流程 ───────────────────────────────────────────────────────────────────

def _stale_exemptions(table, objects) -> list[str]:
    return [f"台账里的对象 `{obj}` 已不在 commands.yaml——只进不出会腐烂"
            for obj in sorted(set(table) - set(objects))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="只列现状不判红（排查用）。⚠ 报绿之前先确认自己跑的是哪种模式")
    args = ap.parse_args()

    table, ledger_errs = load_exemptions()
    reach, orphans, objects = _reachable()
    sources = declaration_sources()

    print(f"=== 联合能力清单：manifest {len(sources['manifest'])} 条 / "
          f"servers.yaml {len(sources['servers_yaml'])} 条 / 端侧 {len(sources['edge'])} 条 ===")
    print(f"=== 端侧车控深检查范围：{len(objects)} 个声明对象，其中 {len(reach)} 个可达 ===")

    lanes = {
        "台账自身": ledger_errs + _stale_exemptions(table, objects),
        "声明源": lane_sources(),
        "执行定义": lane_execution(reach, orphans, objects, table),
        "风险定义": lane_risk(objects),
        "话术定义": lane_speech(reach, table),
        "等价类": lane_equivalence(reach, table),
        "验证定义": lane_verification(reach, table, objects.get),
        "对抗覆盖入口": lane_adversarial(),
    }

    total = 0
    for name, errs in lanes.items():
        total += len(errs)
        mark = "✗" if errs else "✓"
        print(f"\n{mark} {name}（{len(errs)}）")
        for e in errs:
            print(f"    - {e}")

    if args.list:
        print(f"\n[--list 模式] 共 {total} 条，**不判红**。"
              "CI 跑的是默认模式，那一档是阻断的。")
        return 0
    if total:
        print(f"\n✗ 能力完整性检查失败：{total} 条。"
              "照单补齐或进台账显式豁免——**不放宽检查**（案例集是尺子的同款纪律）。")
        return 1
    print("\n✅ PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
