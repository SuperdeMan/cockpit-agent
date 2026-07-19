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


def test_sports_followup_with_yi_chang_not_hijacked_by_nearby():
    """含「那一场」的赛事详情追问 → info.sports（真机漏例，原被 nearby.detail 抢走）。"""
    assert _route("葡萄牙那一场看看详情") == ["info.sports"]
    assert _route("葡萄牙那一场的进球") == ["info.sports"]


def test_sports_followup_prior_phrasings_still_route():
    """既有已修措辞（那场/第N场）不回归。"""
    assert _route("巴西那场帮我看看详情") == ["info.sports"]
    assert _route("第2场进球详情") == ["info.sports"]


def test_real_nearby_detail_still_routes():
    """真·周边详情不被赛事修复误伤。"""
    assert _route("这家怎么样") == ["nearby.detail"]
    assert _route("看看它的详情") == ["nearby.detail"]


def test_sports_predictive_anaphor_routes_to_sports():
    """badcase bfb5d9c7：「这场比赛你预测谁会赢」无联赛词，原预测 hint 接不住 →
    planner 自由发挥缝合幻觉对阵。补「这场/那场 × 预测词」召回 → info.sports
    （handler 内解析焦点场次后让路检索或直接报已完赛赛果）。"""
    assert _route("这场比赛你预测谁会赢") == ["info.sports"]
    assert _route("你预测一下这场谁能赢") == ["info.sports"]
    assert _route("那一场你怎么看") == ["info.sports"]
    # LLM 误路由 info.search 时同样被取代（handler 内部再按需让路检索，语义等价且带锚点）
    assert _route("这场比赛你预测谁会赢", initial=["info.search"]) == ["info.sports"]


def test_predictive_guard_non_sports_not_hijacked():
    """非赛事的「这场…谁会赢/怎么看」不进赛事域。"""
    assert "info.sports" not in _route("这场官司你觉得谁会赢")
    assert "info.sports" not in _route("这场电影你怎么看")
    assert "info.sports" not in _route("这场雨你怎么看")
