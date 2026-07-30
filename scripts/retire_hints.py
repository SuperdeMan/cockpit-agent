"""按跨 provider 交集执行 route_hint 退役（M5 P2 收尾工具）。

`test/hint_retirement.py` 出**判定**，这里做**执行**——两件事分开是刻意的：判定要真栈要
烧 token，执行必须可重放、可 diff、可回滚。

做两件事，缺一不可：
  ① 从 `agents/<x>/manifest.yaml` 删掉该 hint 的 YAML 块，**原地留一条注释**记录退役依据
     （谁、什么时候、几档几轮、命中句多少）——删掉规则不留痕，半年后没人知道为什么没有；
  ② 把该 hint 在 `route_hints_cases.yaml` 里的**召回类**用例迁到 `mode_routing_cases.yaml`
     改端到端口径。**退役的是规则，不是对它的回归保护**——留在原处的断言会变成
     「施加一个不存在的 hint 应得 X」，恒等于什么都没做（vision#0 退役时实测确认）。
     `initial_intents` 非空的是**护栏**用例（断言别的 hint 不劫持），原地不动。

**文本级手术不走 YAML round-trip**：manifest 里注释比代码多（每条 hint 都写着它的出生
badcase），round-trip 会把它们全部抹掉。

  python scripts/retire_hints.py                 # dry-run：打印将做的每一处改动
  python scripts/retire_hints.py --apply
  python scripts/retire_hints.py --only vision,nearby   # 限定 agent
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
_EVAL = _ROOT / "docs" / "reviews" / "eval"
_RH = _ROOT / "test" / "eval_corpus" / "route_hints_cases.yaml"
_MR = _ROOT / "test" / "eval_corpus" / "mode_routing_cases.yaml"
_TZ = timezone(timedelta(hours=8))


def load_intersection(explicit: list[str] | None = None) -> tuple[list[str], dict, list[str]]:
    """→ (交集 key 列表, {key: 证据}, provider 列表)。

    默认只认**全量**报告：局部跑（`.only-`）的 candidates 列表不是完整画面，把它当全量用
    会让「这一档根本没测到」被读成「这一档也判可退役」。
    `explicit`（`--from`）是显式例外——**改完一条 hint 只复验它**是常规动作（本次 nearby
    类目收窄即如此），此时按名给出每一档的局部报告，交集仍要求跨 provider，纪律不降级。
    """
    if explicit:
        files = []
        for s in explicit:
            p = Path(s)
            p = p if p.is_file() else _EVAL / s
            if not p.is_file():
                raise SystemExit(f"✗ 找不到报告 {s}")
            files.append(p)
    else:
        files = sorted(f for f in _EVAL.glob("hint_retirement.*.json")
                       if ".only-" not in f.name)
    if len(files) < 2:
        raise SystemExit(f"✗ 只有 {len(files)} 份报告，跨 provider 交集至少 2 份")
    per, ev, provs = {}, {}, []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        prov = (d.get("meta") or {}).get("provider") or f.stem
        provs.append(prov)
        cands = (d.get("retirement") or {}).get("candidates", [])
        per[prov] = {c["key"] for c in cands}
        for c in cands:
            ev.setdefault(c["key"], {})[prov] = c
    inter = sorted(set.intersection(*per.values()))
    return inter, ev, provs


def _hint_block(text: str, pattern: str) -> tuple[int, int] | None:
    """在 manifest 文本里定位含该 pattern 的那一条 hint 的行区间（含其上方紧邻注释）。"""
    lines = text.splitlines(keepends=True)
    start = None
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("- pattern:") and pattern[:40] in ln:
            start = i
            break
    if start is None:
        return None
    end = start + 1
    while end < len(lines):
        s = lines[end]
        if s.strip() and not s.startswith("    ") and not s.lstrip().startswith("#"):
            break
        if s.lstrip().startswith("- pattern:"):
            break
        end += 1
    return start, end


def retire_from_manifest(agent_id: str, pattern: str, note: str,
                         apply: bool) -> str:
    d = next((p for p in (_ROOT / "agents").glob("*/manifest.yaml")
              if (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("agent_id")
              == agent_id), None)
    if d is None:
        return f"✗ 找不到 {agent_id} 的 manifest"
    text = d.read_text(encoding="utf-8")
    span = _hint_block(text, pattern)
    if span is None:
        return f"✗ {agent_id}: manifest 里找不到该 pattern（已被改过？）"
    lines = text.splitlines(keepends=True)
    removed = "".join(lines[span[0]:span[1]])
    lines[span[0]:span[1]] = [note]
    if apply:
        d.write_text("".join(lines), encoding="utf-8")
    return f"{'✓' if apply else '[dry]'} {agent_id}: 删 {span[1]-span[0]} 行\n" + \
           "".join(f"      - {l}" for l in removed.splitlines(keepends=True)[:3])


def migrate_cases(intent: str, pattern: str, apply: bool) -> tuple[int, list[str]]:
    """route_hints_cases 里「该 hint 的召回用例」→ mode_routing_cases 端到端口径。"""
    rx = re.compile(pattern)
    src = _RH.read_text(encoding="utf-8")
    docs = yaml.safe_load(src) or []
    moved = []
    for c in docs:
        if not isinstance(c, dict):
            continue
        text = str(c.get("text") or "")
        init = list(c.get("initial_intents") or [])
        exp = list(c.get("expect_final_intents") or [])
        # **判据是「hint 有没有改变结果」，不是「initial 空不空」**（首版写错了）：
        #   护栏 = expect == initial（断言 hint **不许**动它）→ 与本 hint 无关，原地不动
        #   召回 = expect != initial（断言 hint **应当**改成 X，initial 模拟 LLM 判错）
        #          → 依赖这条 hint，摘掉后断言恒假，必须迁成端到端口径
        if exp == init:
            continue
        if intent in exp and rx.search(text):
            moved.append((text, exp))
    if not moved or not apply:
        return len(moved), [t for t, _ in moved]
    # 从 route_hints_cases 删（行级，保留注释结构）
    keep, lines, i = [], src.splitlines(keepends=True), 0
    targets = {t for t, _ in moved}
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("- text: ") and ln[len("- text: "):].strip() in targets:
            i += 1
            while i < len(lines) and not lines[i].startswith(("- text:", "#")):
                i += 1
            continue
        keep.append(ln)
        i += 1
    _RH.write_text("".join(keep), encoding="utf-8")
    # 追加到 mode_routing_cases。**去重**：mode_routing_cases 有唯一性自检，同句已在里面
    # （多份语料重叠是常态）就不要再加一份——首版没去重，当场把 eval 弄红了。
    existing = {str(c.get("text") or "") for c in (yaml.safe_load(
        _MR.read_text(encoding="utf-8")) or []) if isinstance(c, dict)}
    moved = [(t, e) for t, e in moved if t not in existing]
    if not moved:
        return 0, []
    blk = [f"\n- text: {t}\n  tags: [mode_guardrail, retired_hint]\n"
           f"  expect_mode: other:{intent}\n  expect_intents: [{intent}]\n"
           f"  source: \"{intent} 的 route_hint 已退役（M5 P2）——回归保护改端到端口径\"\n"
           for t in moved]
    _MR.write_text(_MR.read_text(encoding="utf-8").rstrip() + "\n" + "".join(blk),
                   encoding="utf-8")
    return len(moved), [t for t, _ in moved]


def dependent_det_cases(intent: str, pattern: str) -> list[str]:
    """`mode_routing_cases.yaml` 里**确定性子集**（`expect_det_intents`）对该 hint 的依赖。

    这是 2026-07-30 nearby 退役时发现的工具缺口：`migrate_cases` 只看
    `route_hints_cases.yaml`，而 `mode_routing_cases.yaml` 的 det 子集**也是断言 hint 行为
    的**（`initial_intents: []` + `expect_det_intents: [X]` = 「hint 应当把空计划补成 X」）。
    hint 一退役这类断言就变成「施加一个不存在的 hint 应得 X」——恒假，离线 eval 当场变红，
    而工具一声不响（P2 那批 19 条侥幸没撞上，纯属运气）。

    刻意**只报不改**：新期望值该是多少取决于**剩余 hints 的共同作用**，重算等于在这里
    复刻一份 RouteHintEngine 的语义——两份实现迟早会漂。让人跑一次 eval_mode_routing
    看真实结果再填，是更短也更诚实的路径。
    """
    rx = re.compile(pattern)
    out = []
    for c in (yaml.safe_load(_MR.read_text(encoding="utf-8")) or []):
        if not isinstance(c, dict) or "expect_det_intents" not in c:
            continue
        if intent in list(c.get("expect_det_intents") or []) and rx.search(str(c.get("text") or "")):
            out.append(str(c["text"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="真正落盘（缺省 dry-run）")
    ap.add_argument("--only", default="", help="逗号分隔 agent_id 子串过滤")
    ap.add_argument("--from", dest="from_reports", default="",
                    help="逗号分隔的报告文件名（可含 .only- 局部跑）——用于「只改了某条 hint、"
                         "只复验它」的场景；仍要求 ≥2 档，交集纪律不变")
    args = ap.parse_args()

    inter, ev, provs = load_intersection(
        [s.strip() for s in args.from_reports.split(",") if s.strip()] or None)
    if args.only:
        pats = [s.strip() for s in args.only.split(",") if s.strip()]
        inter = [k for k in inter if any(p in k for p in pats)]
    date = datetime.now(_TZ).strftime("%Y-%m-%d")
    print(f"跨 {len(provs)} 档交集 {len(inter)} 条：{'、'.join(provs)}\n")
    total_moved, applied, skipped = 0, 0, 0
    for key in inter:
        one = next(iter(ev[key].values()))
        agent_id, intent, pattern = one["agent_id"], one["intent"], one["pattern"]
        n_cases = len(one.get("cases") or [])
        if one.get("require_confirm"):
            print(f"⏭ {key} ({intent}) 跳过：require_confirm=true，退役须先过专项安全"
                  f"回归（治理⑥）——路由评测不构成安全证据\n")
            continue
        note = (f"  # 【{intent} 的 route_hint 已于 {date} 退役】判据：双臂裸跑\n"
                f"  #   {' / '.join(provs)} 各 ×2 轮，其 {n_cases} 条命中语料**全覆盖**\n"
                f"  #   （非抽样——抽 3 句会系统性高估，命中面越大的规则越容易被放行）；\n"
                f"  #   摘掉它之后仍全部落对 → 模型 + 范例已经自己会了。\n"
                f"  #   ⚠ 证据只覆盖上述两档。本 hint 家族当初是为 **MiMo** 写的\n"
                f"  #     （route_hints.py 开篇：「弱 LLM（MiMo）常漏/误路由」），而本环境\n"
                f"  #     MiMo 无可用 key（探针 all models failed），**切回 MiMo 须复验**。\n"
                f"  #   回归保护未删：命中句已改端到端口径迁入 mode_routing_cases.yaml。\n"
                f"  #   要恢复：git log -S '{pattern[:24]}' 找回原块。\n")
        applied += 1
        print(retire_from_manifest(agent_id, pattern, note, args.apply))
        n, moved = migrate_cases(intent, pattern, args.apply)
        total_moved += n
        if n:
            print(f"      语料迁移 {n} 条：{'、'.join(moved[:3])}"
                  + ("…" if n > 3 else ""))
        dep = dependent_det_cases(intent, pattern)
        if dep:
            print(f"      ⚠ mode_routing_cases.yaml 还有 {len(dep)} 条 **det 断言**依赖这条 hint："
                  f"{'、'.join(dep[:3])}{'…' if len(dep) > 3 else ''}\n"
                  f"        它们现在断言的是「施加一个不存在的 hint 应得 X」＝恒假。"
                  f"请跑 `python test/eval_mode_routing.py` 看真实结果并改 expect_det_intents"
                  f"（本工具刻意不代改：新值取决于剩余 hints 的共同作用，重算等于复刻一份"
                  f"RouteHintEngine）。")
        print()
    # 报**实际执行数**不是候选数：交集 20 条里有 1 条被 require_confirm 挡下，
    # 汇总行写 20 就是在谎报——这类「数字对不上实际动作」的报数正是本期一路在防的东西。
    print(f"合计：交集 {len(inter)} 条 → **实际退役 {applied} 条**"
          + (f"（{skipped} 条因 require_confirm 跳过）" if skipped else "")
          + f"、迁移 {total_moved} 条语料"
          + ("" if args.apply else "（dry-run，加 --apply 落盘）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
