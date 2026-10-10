"""旅程一轮判定的唯一实现：7 月旅程运行器（`test/e2e_journeys.py`）与 v2 冻结运行器（`scripts/probe_v2_baseline.py`）共用。

纯函数：观测按属性取（`final` 最终帧：speech / ui_card / need_confirm / follow_up；`actions` 本轮动作；`process_events` 过程区事件；
`elapsed` 耗时秒），车况由调用方给一个读取函数——只有判据里写了 `vehicle` 才读，读不到就判失败。这里不发网络请求、不读环境。
设计：docs/design/2026-10-09-v2-core-journey-freeze.md §5；判据键的语义沿用 docs/design/2026-07-14-journey-e2e-test-system.md。
"""
from __future__ import annotations

import json
import re
from typing import Callable, Iterable

#: 一轮 `expect` 认的键；`any_of` 是析取（任一分支全过即过），分支里的键同样受它约束。
EXPECT_KEYS = {"speech_any", "speech_all", "speech_not", "cards_any",
               "card_contains", "need_confirm", "follow_up_any", "action",
               "action_absent", "no_duplicate_action", "process_min",
               "latency_s", "vehicle", "any_of",
               # 冻结 P2 地图家族（2026-10-10）：话术报的全程公里数上下限、候选卡第一项（本地目的地探针的判据搬进来，案例只留一份）
               "route_km_max", "route_km_min", "first_item_any"}

#: 话术里报的全程距离（「全程约 12.5 公里」）。上限：话术不报距离时不判；下限：必须报了且不小于它。
_ROUTE_KM_RE = re.compile(r"全程约\s*([\d.]+)\s*公里")


def card_types(card: dict | None) -> list[str]:
    """收集卡类型，含 card_group 嵌套（items/cards 两种键防御）。"""
    if not card:
        return []
    out = [str(card.get("type", ""))]
    for key in ("items", "cards"):
        for sub in card.get(key) or []:
            if isinstance(sub, dict) and sub.get("type"):
                out.append(str(sub["type"]))
    return out


def check_expect(expect: dict, out, enforce_latency: bool, default_not: Iterable[str],
                 vehicle_reader: Callable[[], dict] | None = None) -> list[str]:
    """一轮的判定：返回不满足的判据（空 = 通过）。`default_not` 是全局禁词，独立于用例 expect。"""
    fails: list[str] = []
    final = getattr(out, "final", None) or {}
    actions = list(getattr(out, "actions", None) or [])
    process_events = list(getattr(out, "process_events", None) or [])
    elapsed = float(getattr(out, "elapsed", 0.0) or 0.0)
    speech = str(final.get("speech", "") or "")
    ctypes = card_types(final.get("ui_card"))
    card_json = json.dumps(final.get("ui_card") or {}, ensure_ascii=False)
    vehicle_cache: dict = {}

    def vehicle() -> dict | None:
        if "state" not in vehicle_cache:
            vehicle_cache["state"] = vehicle_reader() if vehicle_reader else None
        return vehicle_cache["state"]

    def one(exp: dict) -> list[str]:
        f: list[str] = []
        if "speech_any" in exp and not any(str(k) in speech for k in exp["speech_any"]):
            f.append(f"speech_any 未命中 {exp['speech_any']} | speech={speech[:60]}")
        if "speech_all" in exp:
            miss = [k for k in exp["speech_all"] if str(k) not in speech]
            if miss:
                f.append(f"speech_all 缺 {miss} | speech={speech[:60]}")
        for k in exp.get("speech_not", []):
            if str(k) in speech:
                f.append(f"speech_not 命中禁词 {k!r} | speech={speech[:60]}")
        if "cards_any" in exp and not any(t in exp["cards_any"] for t in ctypes):
            f.append(f"cards_any 未命中 {exp['cards_any']} | 实际={ctypes}")
        if "card_contains" in exp:
            miss = [k for k in exp["card_contains"] if str(k) not in card_json]
            if miss:
                f.append(f"card_contains 缺 {miss}")
        if "first_item_any" in exp:
            card = final.get("ui_card") or {}
            items = [i for i in (card.get("items") or []) if isinstance(i, dict)] if isinstance(card, dict) else []
            first = str(items[0].get("name") or "") if items else ""
            if not any(str(k) in first for k in exp["first_item_any"]):
                f.append(f"first_item_any 未命中 {exp['first_item_any']} | 第一项={first[:40]}")
        if "route_km_max" in exp or "route_km_min" in exp:
            km_hit = _ROUTE_KM_RE.search(speech)
            km = float(km_hit.group(1)) if km_hit else None
            if "route_km_max" in exp and km is not None and km > float(exp["route_km_max"]):
                f.append(f"全程 {km} 公里超上限 {exp['route_km_max']} | speech={speech[:60]}")
            if "route_km_min" in exp and (km is None or km < float(exp["route_km_min"])):
                f.append(f"全程 {km} 公里不到下限 {exp['route_km_min']} | speech={speech[:60]}")
        if "need_confirm" in exp and bool(final.get("need_confirm")) != bool(exp["need_confirm"]):
            f.append(f"need_confirm={final.get('need_confirm')} 期望 {exp['need_confirm']}")
        if "follow_up_any" in exp:
            fu = str(final.get("follow_up", "") or "")
            if not any(str(k) in fu for k in exp["follow_up_any"]):
                f.append(f"follow_up_any 未命中 {exp['follow_up_any']} | {fu[:40]}")
        if "action" in exp:
            specs = exp["action"] if isinstance(exp["action"], list) else [exp["action"]]
            for spec in specs:
                hit = None
                for a in actions:
                    if str(a.get("type", "")) != str(spec.get("type", "")):
                        continue
                    payload = a.get("payload") or {}
                    if any(k not in payload for k in spec.get("payload_has", [])):
                        continue
                    pm = spec.get("payload_match", {})
                    if any(str(pm[k]) not in json.dumps(payload.get(k, ""), ensure_ascii=False)
                           for k in pm):
                        continue
                    hit = a
                    break
                if hit is None:
                    f.append(f"action 未命中 {spec} | 实际类型={[a.get('type') for a in actions]}")
        for atype in exp.get("action_absent", []):
            if any(a.get("type") == atype for a in actions):
                f.append(f"不该出现的动作 {atype} 出现了")
        for atype in exp.get("no_duplicate_action", []):
            n = sum(1 for a in actions if a.get("type") == atype)
            if n > 1:
                f.append(f"动作 {atype} 重复 {n} 次")
        if "process_min" in exp and len(process_events) < int(exp["process_min"]):
            f.append(f"过程区事件 {len(process_events)} < {exp['process_min']}")
        if "latency_s" in exp and enforce_latency and elapsed > float(exp["latency_s"]):
            f.append(f"时延 {elapsed:.1f}s 超预算 {exp['latency_s']}s")
        if "vehicle" in exp:
            st = vehicle()
            if st is None:
                f.append("车况读不到（调用方没给读取函数）")
            else:
                for k, v in exp["vehicle"].items():
                    if st.get(k) != v:
                        f.append(f"车况 {k}={st.get(k)!r} 期望 {v!r}")
        if "any_of" in exp:
            subs = [one(s) for s in exp["any_of"]]
            if all(subs):
                f.append("any_of 全部分支未满足: " + " || ".join(
                    ";".join(s)[:80] for s in subs))
        return f

    fails.extend(one(expect))
    for k in default_not:                     # 全局红线独立于用例 expect
        if k in speech:
            fails.append(f"全局禁词命中 {k!r} | speech={speech[:60]}")
    return fails
