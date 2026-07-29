"""赛事追问 vs 周边发现 路由回归——对真实 agents/*/manifest.yaml 验证 RouteHintEngine。

真机漏例（2026-07-07）：「葡萄牙那一场看看详情」被 nearby.detail 的「看…详情」劫持，未走赛事详情。
根因=info.sports pattern `(那|这|上一?|哪)\\s*场` 与 nearby.detail guard `那场|…` 都漏「那一场」
（中间的「一」）。防御纵深两边都补可选「一」。本测试锁死该修复。
"""
import glob
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_gen_py = _ROOT / "gen" / "python"
if _gen_py.is_dir():
    sys.path.insert(0, str(_gen_py))

from orchestrator.cloud.route_hints import RouteHintEngine  # noqa: E402
from orchestrator.cloud.models import Plan, Step  # noqa: E402
from agents._sdk.manifest import load_manifest  # noqa: E402


def _agent_map() -> dict:
    """加载全部真实 manifest（模拟生产：所有 Agent 的 route_hints 一起竞争）。"""
    amap = {}
    for path in glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml")):
        m = load_manifest(path)
        amap[m.agent_id] = SimpleNamespace(manifest=m, endpoint=f"{m.agent_id}:0")
    return amap


def _validate(raws, agent_map):
    return [Step(id=r["id"], agent_id=r["agent_id"], intent=r["intent"], slots=dict(r["slots"]))
            for r in raws]


_AMAP = _agent_map()
_ENGINE = RouteHintEngine(_validate)


def _route(text: str, initial=None) -> list[str]:
    plan = Plan(steps=[Step(id=f"s{i}", agent_id="_seed", intent=it)
                       for i, it in enumerate(initial or [])])
    _ENGINE.apply(plan, text, _AMAP)
    return [s.intent for s in plan.steps]






def test_predictive_guard_non_sports_not_hijacked():
    """非赛事的「这场…谁会赢/怎么看」不进赛事域。"""
    assert "info.sports" not in _route("这场官司你觉得谁会赢")
    assert "info.sports" not in _route("这场电影你怎么看")
    assert "info.sports" not in _route("这场雨你怎么看")

# ── M5 P2 退役记录（2026-07-29）────────────────────────────────────────────────

def test_retired_hints_are_gone_by_design():
    """上方原有的「hint 应当补步/改写」断言已删除——它们描述的机制不存在了。

    退役的 hint（跨 minimax:MiniMax-M3 与 deepseek:deepseek-v4-flash 两档、全部命中语料
    全覆盖、各 ×2 轮，摘掉后仍全部落对）：info#0/#3（info.sports）、nearby#2（nearby.detail）

    **回归保护去哪了，以及它变弱了多少**：命中句已改端到端口径迁入
    `test/eval_corpus/mode_routing_cases.yaml`，由 `eval_mode_routing --live` 覆盖。
    但那是 **live 车道、不在 CI**——原来这些断言被刻意写成阻断 pytest，理由白纸黑字：
    「eval_route_hints 语料在 continue-on-error 观测步，语料回归 CI 不红」。
    所以退役把这部分**召回保护从「CI 阻断」降级成了「人工触发」**。
    这是退役的真实代价，不是可以忽略的细节；要补回来得让 live 车道进 CI（真栈 + LLM，
    成本另议）——已作为余项记在 RFC §5-P2。

    本测试守住两件仍可离线验证的事：①这些 intent 作为**能力**依然存在（域没有消失，
    只是不再有正则替模型做决定）；②没人在没有新证据的情况下把 hint 悄悄加回来。
    """
    import glob as _glob
    import pathlib as _pl

    import yaml as _yaml
    root = _pl.Path(__file__).resolve().parents[1]
    retired = ['info.sports', 'nearby.detail']
    caps, hints = set(), {}
    for p in sorted(_glob.glob(str(root / "agents" / "*" / "manifest.yaml"))):
        d = _yaml.safe_load(open(p, encoding="utf-8")) or {}
        caps |= {str(c.get("intent")) for c in (d.get("capabilities") or [])}
        for h in (d.get("route_hints") or []):
            hints.setdefault(str(h.get("intent")), []).append(d.get("agent_id"))
    for i in retired:
        assert i in caps or i.startswith("shop."), f"{i} 的能力也消失了——退役只该去掉规则"
        assert i not in hints, (
            f"{i} 的 route_hint 又回来了（{hints.get(i)}）——恢复规则需要新证据："
            f"双臂裸跑显示模型自己做不到，且要写进 manifest 注释")
