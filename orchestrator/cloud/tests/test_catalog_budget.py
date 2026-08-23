"""D1 契约测试（数据飞轮 P0）：catalog 预算裁剪对**真实 manifests** 的实际行为。

固化三个事实（docs/design/2026-07-28-intent-accuracy-data-flywheel.md §3-D1）：
1. 旧默认 8000 字符下，满栈（14 云 manifest + builtin-tools + 2 端）渲染仍超预算——「正常情况下根本
   不触发裁剪」的旧假设已随 M3/M4 能力面增长失效。
2. **P0 时被裁的恰是全部「无 route_hints」的 agent，含核心域 navigation**——保护资格
   是「有没有声明 hint」这个巧合，与领域重要性无关。**M5 P2 已修**：保护判据补上
   `category: core`（见 `context.py::_always_include`），navigation / road-safety 自此
   不再被裁；本测试相应改断言「被裁集合 = 非 core 且无 hint 的 agent」，**并显式断言
   核心域不在其中**——那条才是当初真正想守的性质。
3. 当前默认 16000 下全量放得下、零裁剪。若能力面继续增长让本测试转红，说明预算
   又被追上——正确动作是启用 catalog 检索化预筛，不是回到静默丢域。
"""
from __future__ import annotations

import json
import os
import sys

import yaml

# 端侧模块按服务内裸名导入（edge tests 同款惯例）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "edge"))

import orchestrator.cloud.context as ctxmod
from orchestrator.cloud.context import WorkingSet

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
# 既非 core、又没声明 route_hints 的 agent——预算不够时只有它们该被裁
#
# 2026-07-30 `nearby` 加入本集合：它的两条 route_hints 已由数据退役（跨两档全覆盖双臂裸跑），
# 而它是 `category: ecosystem`（trust_level third_party，高德 POI）——**保护是随 hint 一起
# 没的**。这正是 P2 记过的那个副作用「hint 退役会顺手删掉那个 Agent 的 catalog 保护」，
# 本次是**预期发生**，故改断言而不是回避：
#   ① 现默认预算 16000 下全量零裁剪，生产无影响（下一个测试守着）；
#   ② **绝不为了拿保护而留一条规则**——那就是本期在清理的巧合耦合，方向反了；
#   ③ 也不把 nearby 改标 `core` 去骗保护：`ecosystem` 是它的诚实分类，为副作用改字段
#      等于把 category 变成第二个「有 hint 就保护」；
#   ④ 但要记一笔风险：若预算再被追上，被裁的将包含**周边发现这个高频功能**。
#      正确动作是启用 catalog 检索化预筛（RFC §5-P2-4），不是回填规则或改分类。
_UNPROTECTED = {"manual-rag", "parking-payment", "nearby"}
# 核心域：无论预算多紧都不许被裁（P0 时 navigation/road-safety 恰恰会被裁，那是 D1 根因）
_CORE_MUST_SURVIVE = {"navigation", "road-safety", "info", "reminder",
                      "scene-orchestrator", "vision", "charging-planner"}


def _full_stack_agents() -> list:
    test_dir = os.path.join(_ROOT, "test")
    if test_dir not in sys.path:
        sys.path.insert(0, test_dir)
    import eval_live
    return eval_live.load_agents(include_edge=True)


def test_budget_full_stack_matches_eval_live_runtime_inventory():
    test_dir = os.path.join(_ROOT, "test")
    if test_dir not in sys.path:
        sys.path.insert(0, test_dir)
    import eval_live

    budget_agents = _full_stack_agents()
    live_agents = eval_live.load_agents(include_edge=True)
    budget_ids = [agent.manifest.agent_id for agent in budget_agents]
    live_ids = [agent.manifest.agent_id for agent in live_agents]
    assert budget_ids == live_ids
    assert len(budget_ids) == len(set(budget_ids)) == 17
    assert "builtin-tools" in budget_ids


def test_eval_live_edge_inventory_is_the_production_registered_manifest():
    """评测不能手抄 edge 能力；否则生产新增 route hint 而 gate 永远测不到。"""
    test_dir = os.path.join(_ROOT, "test")
    if test_dir not in sys.path:
        sys.path.insert(0, test_dir)
    import eval_live
    from capabilities import build_edge_manifests

    actual = {
        agent.manifest.agent_id: agent.manifest
        for agent in eval_live.load_agents(include_edge=True)
        if agent.manifest.agent_id.startswith("edge-")
    }
    expected = {manifest.agent_id: manifest for manifest in build_edge_manifests()}

    assert actual == expected
    assert {hint.intent for hint in actual["edge-vehicle"].route_hints} == {
        "door_lock.open", "door_lock.close",
    }


def test_tight_budget_only_drops_unprotected_and_never_core(monkeypatch):
    """预算再紧也只裁「非 core 且无 hint」的 agent；**核心域一个都不许掉**。

    这条断言是 M5 P2 的成果：同样的 8000 预算下，P0 时 navigation 与 road-safety
    会被整域裁出 prompt（planner 从此看不见它们、步骤校验还会拒它们的 intent），
    现在它们由 `category: core` 保住。"""
    monkeypatch.setattr(ctxmod, "_CATALOG_BUDGET", 8000)
    stats: dict = {}
    WorkingSet.render_catalog(_full_stack_agents(), stats)
    assert stats["chars_full"] > 8000
    dropped = set(stats["dropped"])
    assert dropped == _UNPROTECTED
    assert not (dropped & _CORE_MUST_SURVIVE), f"核心域被裁：{dropped & _CORE_MUST_SURVIVE}"


def test_current_default_budget_holds_full_stack(monkeypatch):
    monkeypatch.setattr(ctxmod, "_CATALOG_BUDGET", 16000)   # 显式=代码默认，免受宿主 env 影响
    stats: dict = {}
    WorkingSet.render_catalog(_full_stack_agents(), stats)
    assert stats["dropped"] == []
    assert stats["chars_full"] == stats["chars_final"]


def test_request_ref_mapping_holds_the_real_live_inventory(monkeypatch):
    """Ref wire groups agent metadata once and must not evict default live domains."""
    test_dir = os.path.join(_ROOT, "test")
    if test_dir not in sys.path:
        sys.path.insert(0, test_dir)
    import eval_live
    from orchestrator.cloud.planning import _assemble_capability_catalog
    from orchestrator.cloud.tools import ToolRegistry

    monkeypatch.setattr(ctxmod, "_CATALOG_BUDGET", 16000)
    agents = eval_live.load_agents(include_edge=True)
    catalog = _assemble_capability_catalog(agents)

    assert len(agents) == 17
    # 2026-08-12 138→141：三条受审复合商户能力激活；官方低层工具仍不入 catalog。
    # 2026-08-13 141→142：新增 `luckin.menu`（真实门店只读菜单）——在它之前
    # 「这家店的菜单」在目录里只对应演示商户 shop.menu，真实门店问句因此答出演示数据。
    # 2026-08-13 142→143：新增当店菜单 `mcd.menu`（营养表改名 mcd.nutrition 让位）——
    # 是**改名+新增**而不是净增两条，所以只 +1。
    # 2026-08-15 143→144：G8 新增 `navigation.reroute`（增量改道——焦点 active_route
    # 在场时「途经点不去了/换条路」改的是这次导航，此前被兑现成全新导航）。
    # 2026-08-15 144→145：卡 Q9 新增 `safety.driver_state`（疲劳/饮酒/身体不适时
    # 能否继续驾驶）。**这一条是「改实现不等于加能力」的兑现物**——road-safety 里
    # 的确定性疲劳判据先落了地，但没有 manifest 声明时 planner 根本路由不过来，
    # 「困到睁不开眼了」三次取样分别落到闲聊、拒识和音量调节（迷你集 SF4）。
    # 2026-08-19 145→151：卡 Q8「能力缺席 → 就近误执行」一次补齐 **6 条**——
    # 云侧 `navigation.estimate`（只算不导：此前「A 到 B 多远多久」被就近挑成
    # navigate_to **真的开始导航**）、`navigation.cancel`（终止本次导航：此前
    # 「取消导航」被前置闸当成「取消挂起」吞掉），端侧 `volume.mute`/`volume.unmute`
    # （此前「静音」上云被映射成 volume.dec）与 `warning_light.open`/`.close`
    # （此前「打开双闪」三次取样分别落 power_mode.set / lane_assistance.close /
    # **hvac.off**——要开双闪把空调关了）。端侧四条同样进 catalog：planner 看得见的
    # 车控工具就是这一份。
    # 2026-08-24 151→152：SL1 新增 `reminder.create_batch`。同一事项两个时刻是
    # 一个原子业务意图，不能再赌 planner 恰好生成两个步骤、也不能让两次独立写入
    # 落成半组。其余本批 route hint 都指向既有能力，不增加目录条数。
    assert len(catalog.ref_to_pair) == 152
    assert catalog.catalog_stats["dropped"] == []
    # object-key wire 去掉每项重复字段名后，完整生产 inventory 精确占用 10865。
    # info.sports 新增过去赛果/泛指赛事边界后增加 49 字符，仍完整落在 16k 预算内。
    # 2026-08-10：端侧补前/后挡除雾 4 条能力（131→135），+140 字符，余量 5275→5135。
    # 2026-08-11：真实商户 MCP v1 激活 3 条（135→138），+216 字符，余量 5135→4919
    # ——有意新增（mcd.menu/mcd.order_status/luckin.order_status，见 servers.yaml）。
    # 同日批 3c 再 +37：demo shop.order 描述判别化加长（端到端实锤 planner 把品牌
    # 下单话术塞给 demo 的真因是描述太像通用点餐——描述加限定是修法本体）。
    # 这个数**该跟着能力面走**——它守的是「完整 inventory 仍不超预算」，不是冻结条数；
    # 但每次动它都要先确认涨的是有意新增的能力，而不是别处漏进来的重复项。
    # 2026-08-12：新增 mcd.order/luckin.order/luckin.order_cancel，且瑞幸下单描述明确锁定
    # nearby.search 可信公开 POI 依赖；完整 inventory 精确增加到 11529，仍未发生预算裁剪。
    # 2026-08-13：新增 luckin.menu（只读门店菜单）+143 字符 → 11672，`dropped` 仍为空。
    # 同日再 +117 → 11789：麦当劳当店菜单 mcd.menu 上线 + 营养表改名并把描述写诚实
    # （「不含价格，问价格请用当店菜单」——旧描述让「多少钱」只能落到营养表）。
    # 16k 预算余量 4211。按上面那条纪律核过：涨的是有意新增的能力，不是重复项。
    # 2026-08-13 demo-mkemhn 收口再 +39 → 11828：shop.menu 描述补真实品牌排除条款
    # （「这家的菜单」在焦点是瑞幸门店时仍被规划到演示商户 shop.menu，trace c4a82439
    # ——同 shop.order 那次「描述太像通用能力」的第二例，描述判别化是修法本体）。
    # 涨的是既有能力的判别化描述，不是新增条目，条数 143 不变。
    # 2026-08-13 demo-3ukshz 二轮再 +38 → 11866：mcd.menu 描述改写（「附近的麦当劳」
    # 需先经周边搜索取门店名——旧描述「不给门店时就近选一家」是**假承诺**，桥拿不到
    # 位置、给的是商户默认店）+ 新增 category 槽（分类导航）。条数 143 仍不变。
    # 2026-08-14 EVA 二轮净 +62 → 11928：navigate_to 新增 arrive_by/route_pref 槽
    # 与判别化描述（时间约束/路线偏好教给 planner），减去批 A 摘除的死槽位
    # （nearby radius/price_level/datetime/party_size、charging departure_time）。
    # 条数 143 不变——涨的是有意新增的能力面，不是重复项。
    # 2026-08-15 +321 → 12249：G8 `navigation.reroute` 上目录（判别化描述写清与
    # trip.modify 的边界：这一趟在开的路线 vs 多日行程的第 N 天）。有意新增 +1 条。
    # 同日 +91 → 12340：G4 trip.plan 加 theme 槽 + 主题行程判别化描述
    # （「跟着《某剧》游X」进目录；「只聊作品内容」显式排除）。条数 144 不变。
    # 同日 +78 → 12418：G9 trip.plan 多城市描述（「先去A再去B」城市按口述序连写
    # 进 destination，保序逐城安排+跨城驾驶段）。条数 144 不变。
    # 同日 +90 → 12508：P2 trip.plan 加 must_visit 槽+点名地点描述（「东方之门/
    # 大秋裤、灵山大佛」逐个接地务必编入行程）。条数 144 不变。
    # 2026-08-15 +107 → 12615：卡 Q9 新增 `safety.driver_state`（判别化描述写清
    # 与 driving_advice 的边界：约束在**人**（疲劳/饮酒/不适）还是在**环境**
    # （天气/路况）——两侧共用「能不能开」这个框架，不写清就一锅端）。有意新增 +1 条。
    # 2026-08-19 +524 → 13139：卡 Q8 六条能力上目录，其中三条的描述刻意写长——
    # 它们要教给 planner 的都是**边界**而不是功能：`navigation.estimate` 与
    # navigate_to 的边界是「用户要的是数还是行程」（不写清就会把纯查询兑现成
    # 真导航）、`navigation.cancel` 与 reroute 的边界是「终止 vs 增量调整」、
    # `volume.mute` 与 volume.dec / media.pause 的边界是「压掉全部出声 vs 调小 vs
    # 停播放」（真栈实测「静音」正是落到 volume.dec）。端侧两条走机械生成的短描述。
    # 2026-08-21 +23 → 13162：Q12 规格维给 `luckin.order` 补 `size` 槽（杯型）
    # 并在描述里点名规格面。**这一笔是「补一维已经在被用户说、却无处可放的能力」**
    # ——planner 真栈实测本来就在产 `size: 大杯`，契约里没有这个槽，值被静默丢掉。
    # 条数不变（既有 workflow 加槽，不是新增条目）。
    # 2026-08-24 +116 → 13278：`reminder.create_batch` 的能力描述与一条判别化范例。
    # 有意新增 +1 条；默认 16k 下仍零裁剪。
    assert catalog.catalog_stats["chars_full"] == 13278
    assert catalog.catalog_stats["chars_final"] == 13278
    assert catalog.catalog_stats["chars_final"] == len(catalog.semantic_mapping_text)
    assert catalog.catalog_stats["chars_final"] <= 16000
    # 余量随目录一起走（13278 → 2722）。这行的意义不是「余量是多少」，
    # 是**每次加能力都必须把余量重新看一眼**——16k 预算被撑满时该做的是
    # 检索化 catalog，不是悄悄放大预算（§4.2 M5 后续杠杆）。
    assert 16000 - catalog.catalog_stats["chars_final"] == 2722
    assert set(catalog.agent_map) == {a.manifest.agent_id for a in agents}
    assert {"parking-payment", "nearby", "manual-rag"} <= set(catalog.agent_map)
    builtin = catalog.agent_map["builtin-tools"].manifest
    assert builtin == ToolRegistry().manifest
    assert builtin.kind == "tool"
    assert builtin.deployment == "cloud"
    assert {cap.intent for cap in builtin.capabilities} == {
        "datetime.parse", "unit.convert", "math.eval",
    }

    groups = [json.loads(line) for line in catalog.semantic_mapping_text.splitlines()[1:]]
    assert len(groups) == len(agents)
    assert all(set(group) == {
        "service", "kind", "deployment", "trust", "capabilities",
    } for group in groups)
    assert all(isinstance(group["capabilities"], dict) for group in groups)
    capability_refs = {
        ref for group in groups for ref in group["capabilities"]
    }
    assert capability_refs == set(catalog.ref_to_pair)
    for group in groups:
        expected_value_length = 1 if group["service"].startswith("edge-") else 3
        assert all(
            isinstance(value, list) and len(value) == expected_value_length
            for value in group["capabilities"].values()
        )


def test_merchant_workflow_capabilities_are_in_catalog_without_internal_tools():
    """复合商户 intent 是用户能力；官方低层工具只作内部依赖，不得进入 catalog。"""
    import eval_live

    intents = eval_live.known_intents()
    assert {"mcd.order", "mcd.menu", "mcd.nutrition", "luckin.order",
            "luckin.order_cancel", "luckin.menu"} <= intents
    assert not ({
        "query-nearby-stores", "query-meals", "create-order",
        "queryShopList", "searchProductForMcp", "createOrder", "cancelOrder",
    } & intents)


def test_charging_catalog_exposes_depleted_help_and_status_boundary():
    manifest_path = os.path.join(
        _ROOT, "agents", "charging_planner", "manifest.yaml")
    with open(manifest_path, encoding="utf-8") as stream:
        manifest = yaml.safe_load(stream)
    capabilities = {row["intent"]: row for row in manifest["capabilities"]}

    find = capabilities["charging.find"]
    status = capabilities["charging.status"]
    assert "见底" in find["description"]
    assert "补能求助" in find["description"]
    assert "因电量耗尽无法行驶" in find["description"]
    assert "趴窝" not in find["description"]
    assert "没有询问词" in find["description"]
    assert "绝不归 charging.status" in find["description"]
    assert all(boundary in status["description"] for boundary in (
        "明确询问", "百分比", "剩余续航", "充电状态",
        "补能求助不归此能力", "禁止归此能力",
    ))

    from orchestrator.cloud.planning import _assemble_capability_catalog
    catalog = _assemble_capability_catalog(_full_stack_agents())
    groups = [json.loads(line) for line in
              catalog.semantic_mapping_text.splitlines()[1:]]
    charging = next(group for group in groups
                    if group["service"] == "charging-planner")
    find_ref = catalog.pair_to_ref[("charging-planner", "charging.find")]
    status_ref = catalog.pair_to_ref[("charging-planner", "charging.status")]
    assert "见底" in charging["capabilities"][find_ref][2]
    assert "因电量耗尽无法行驶" in charging["capabilities"][find_ref][2]
    assert "趴窝" not in charging["capabilities"][find_ref][2]
    assert all(boundary in charging["capabilities"][status_ref][2]
               for boundary in (
                   "百分比", "补能求助不归此能力", "禁止归此能力",
               ))


def test_sports_catalog_owns_generic_completed_match_results():
    manifest_path = os.path.join(_ROOT, "agents", "info", "manifest.yaml")
    with open(manifest_path, encoding="utf-8") as stream:
        manifest = yaml.safe_load(stream)
    capabilities = {row["intent"]: row for row in manifest["capabilities"]}
    description = capabilities["info.sports"]["description"]

    for boundary in ("已结束", "昨天/前天/昨晚", "那场比赛", "没说联赛或球队"):
        assert boundary in description
    assert "通用联网搜索不是替代路由" in description


def test_eval_live_inventory_always_has_one_builtin_tools_agent():
    test_dir = os.path.join(_ROOT, "test")
    if test_dir not in sys.path:
        sys.path.insert(0, test_dir)
    import eval_live

    for include_edge, expected_count in ((False, 15), (True, 17)):
        agents = eval_live.load_agents(include_edge=include_edge)
        builtin = [agent for agent in agents
                   if agent.manifest.agent_id == "builtin-tools"]
        assert len(agents) == expected_count
        assert len(builtin) == 1
        assert builtin[0].manifest.deployment == "cloud"
        assert {cap.intent for cap in builtin[0].manifest.capabilities} == {
            "datetime.parse", "unit.convert", "math.eval",
        }
    assert {"datetime.parse", "unit.convert", "math.eval"} <= eval_live.known_intents()


def test_eval_live_tool_registry_replaces_conflicting_static_builtin(monkeypatch):
    test_dir = os.path.join(_ROOT, "test")
    if test_dir not in sys.path:
        sys.path.insert(0, test_dir)
    import eval_live
    from agents._sdk import manifest as manifest_module
    from cockpit.agent.v1 import agent_pb2
    from orchestrator.cloud.tools import ToolRegistry

    conflicting = agent_pb2.AgentManifest()
    conflicting.CopyFrom(ToolRegistry().manifest)
    conflicting.version = "static-conflict"
    fake_path = os.path.join(_ROOT, "agents", "builtin_tools", "manifest.yaml")
    monkeypatch.setattr(eval_live.glob, "glob", lambda _pattern: [fake_path])
    monkeypatch.setattr(manifest_module, "load_manifest", lambda _path: conflicting)

    agents = eval_live.load_agents(include_edge=False)
    builtin = [agent for agent in agents
               if agent.manifest.agent_id == "builtin-tools"]
    assert len(builtin) == 1
    assert builtin[0].manifest == ToolRegistry().manifest
    assert builtin[0].endpoint == "tool://builtin"


def test_edge_capabilities_stay_name_only_in_catalog(monkeypatch):
    """端侧能力（2026-08-04 起 76 条）在 catalog 里**只出名字**——判别化描述有意不进 planner prompt。

    这条是 M5 P3 收尾的**负结果护栏**。`capabilities.py` 已经能机械生成逐条判别化
    描述了，把它们渲进 catalog 看起来天经地义；实测双臂差分（唯一变量=这一个渲染分支，
    25 条 canonical+口语语料 ×2 轮 ×2 provider）**Δ=0、100 次对照零翻面**，而代价是每次
    规划 +1462 字符。判别化描述真正的受益方是 registry 语义兜底（按 capability 粒度
    embed），不是 planner。

    所以本条守的不是「省字符」，是**不许在没有证据的情况下把成本加进每一次规划**。
    要推翻它，请附上跨 provider 的双臂数据，而不是「描述当然比没描述好」。
    """
    monkeypatch.setattr(ctxmod, "_CATALOG_BUDGET", 16000)
    stats: dict = {}
    rendered = json.loads(WorkingSet.render_catalog(_full_stack_agents(), stats))
    edge = [i for i in rendered if i["agent_id"].startswith("edge-")]
    assert len(edge) == 2
    caps = [c for item in edge for c in item["capabilities"]]
    assert len(caps) >= 70, "端侧能力面塌了？"     # 不钉死数字：新增车控意图是正常演进
    assert all(set(c) == {"intent"} for c in caps), "端侧能力多渲染了字段，请附 A/B 证据"
    assert stats["dropped"] == []

    from orchestrator.cloud.planning import _assemble_capability_catalog
    request_catalog = _assemble_capability_catalog(_full_stack_agents())
    request_groups = [json.loads(line) for line in
                      request_catalog.semantic_mapping_text.splitlines()[1:]]
    request_edge = [group for group in request_groups
                    if group["service"].startswith("edge-")]
    request_caps = [value for group in request_edge
                    for value in group["capabilities"].values()]
    assert len(request_caps) >= 70
    assert all(len(value) == 1 for value in request_caps), (
        "请求级 ref mapping 给端侧能力增加了未证明有效的语义字段")
    assert request_catalog.catalog_stats["dropped"] == []
