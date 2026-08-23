"""Q10 · 文本入口与按钮入口收敛到同一条解析链（I-020 / I-025①，2026-08-19）。

立卡时的说法是「按钮路径带结构化引用（store 三元组、product_code），文本路径靠
LLM 从原话解析」。**取证把这句话改了**：按钮送出的就是一句中文
（`ui_card.options[].send_text` = `在<门店>点一份<商品全名>`），里面没有任何 id。
两条入口真正的差别是**用词是不是商家的原名**——

  · 点按钮 → `item_query="巨无霸套餐"` → `_matching_products` 精确命中一款；
  · 说「巨无霸」→ 多命中或零命中 → 追问 / 跳回别的列表（I-020）；
  · 说「第一杯」→ 拿去商户接口当关键词搜 → 搜不到（I-025①）。

所以收敛的目标就是**那个规范名**。本文件两头都钉：

  ① 判据本身（`candidate_ref`，确定性纯函数）；
  ② **挂点真的接上了**——`McpBridgeAgent.handle` 调它、workflow 收到的是翻译后的
     槽值。§58.3 已经为「只测纯函数、没测挂点」付过第四次学费（I-052 那条守卫
     上线时挂点从没验过），这里不重犯。

⚠ 反向也钉：确认/取消路径**不许**翻译（那条路靠 checkout_token 寻址，草稿里的商品
早已核价定死），跨商户候选**不许**被采纳（否则「附近的瑞幸」之后一句「点第一个」
会把一个高德 POI 名当成商品名）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents._sdk.testing import make_context, run_handle
from agents.mcp_bridge.src import candidate_ref
from agents.mcp_bridge.src.admission import load_servers
from agents.mcp_bridge.src.agent import McpBridgeAgent
from orchestrator.cloud.context import candidate_downlink

_MENU = [
    {"index": 1, "name": "北非蛋风味麦满分套餐"},
    {"index": 2, "name": "猪柳蛋麦满分套餐"},
    {"index": 3, "name": "巨无霸套餐"},
]


def _meta(items=None, source_intent="mcd.menu", **extra):
    payload = {"source_intent": source_intent,
               "items": list(items if items is not None else _MENU)}
    return {candidate_ref.META_KEY: json.dumps(payload, ensure_ascii=False),
            **extra}


# ── A. 三条通道各自成立 ──────────────────────────────────────────────
@pytest.mark.parametrize("value,expected", [
    ("巨无霸套餐", "巨无霸套餐"),          # 原名：按钮走的就是这条，幂等
    ("第一个", "北非蛋风味麦满分套餐"),      # 序数（通用量词）
    ("第一杯", "北非蛋风味麦满分套餐"),      # 序数（商户面量词）
    ("第 2 个", "猪柳蛋麦满分套餐"),         # 阿拉伯数字 + 空格
    ("第三", "巨无霸套餐"),                 # 量词可省
    ("巨无霸", "巨无霸套餐"),               # 唯一部分名（I-020 原话）
])
def test_three_channels_resolve_to_the_official_name(value, expected):
    got = candidate_ref.resolve(value, _meta(), namespace="mcd")
    assert got is not None and got["name"] == expected, value


# ── B. 判不出就不动——翻错比不翻更糟 ────────────────────────────────
@pytest.mark.parametrize("value", [
    "麦满分",              # 命中两项：交回商户既有的选项卡，不猜其中一个
    "第十个",              # 越界
    "第零个",              # 0 不是序号
    "可",                  # 单字：命中面太大（「可」会命中「可乐」「可颂」）
    "拿铁",                # 一项都不命中
    "第一个和第二个",       # 聚合问句——归云侧 candidate_query，这里不选其中一个
    "",
])
def test_ambiguous_or_unknown_leaves_the_slot_alone(value):
    assert candidate_ref.resolve(value, _meta(), namespace="mcd") is None, value


def test_a_product_whose_own_name_contains_an_ordinal_is_not_read_as_one():
    """「生椰拿铁第二杯半价」里的「第二」是名字的一部分，不是序号。

    两道判据一起保证：序数**锚在槽值开头**（同 `context._CANDIDATE_REFERENCE_RE`
    那条纪律），且原名通道排在序数通道前面。
    """
    items = [{"index": 1, "name": "生椰拿铁"},
             {"index": 2, "name": "美式"},
             {"index": 3, "name": "生椰拿铁第二杯半价"}]
    got = candidate_ref.resolve("生椰拿铁第二杯半价", _meta(items, "luckin.menu"),
                                namespace="luckin")
    assert got is not None and got["index"] == 3


# ── C. 归属：只认自己那家商户产出的候选 ──────────────────────────────
def test_another_merchants_candidate_set_is_not_consumed():
    assert candidate_ref.resolve("第一个", _meta(source_intent="mcd.menu"),
                                 namespace="luckin") is None


def test_a_nearby_poi_list_is_not_consumed_as_a_product_list():
    """「先查附近的瑞幸」→「点第一个」：那份候选是 POI 不是商品。

    没有这条归属判据，`item_query` 会被填成一个**用户从没说过的商品名**——
    同 §4.3「认不出就返回空，绝不回落到某一档」。
    """
    poi = [{"index": 1, "name": "瑞幸咖啡(科苑南路店)"}]
    assert candidate_ref.resolve("第一个", _meta(poi, "nearby.search"),
                                 namespace="luckin") is None


# ── D. 损坏/缺失的下发一律丢弃，不猜 ────────────────────────────────
@pytest.mark.parametrize("meta", [
    {},
    {candidate_ref.META_KEY: ""},
    {candidate_ref.META_KEY: "not json"},
    {candidate_ref.META_KEY: "[]"},
    {candidate_ref.META_KEY: json.dumps({"items": []})},
    {candidate_ref.META_KEY: json.dumps({"items": [{"name": "甲"}]})},   # 缺 index
    {candidate_ref.META_KEY: json.dumps({"items": [{"index": 1}]})},     # 缺 name
])
def test_malformed_downlink_is_discarded(meta):
    assert candidate_ref.parse(meta) is None


# ── E. 跨层形状契约：消费方的期望**从产生方派生** ────────────────────
def test_the_consumer_parses_exactly_what_the_producer_emits():
    """§58.1 第一例的正解：白名单/形状是**与产生方的契约**，不能照直觉猜。

    那一次 `_CANDIDATE_ITEM_KEYS` 里 7 个键与任何产生方都对不上，而
    `test_candidate_sets.py` 的 fixture 也写着同一个不存在的字段名——
    **测试替被测系统提供了前提**。这里反过来：直接拿云侧下发投影的真实产出
    喂给桥的解析器，两端对不上就红。
    """
    produced = candidate_downlink({
        "source_intent": "mcd.menu",
        "items": [{"name": "巨无霸套餐", "price": "36.90 元", "id": "M001"},
                  {"name": "双层吉士堡套餐", "price": "32.90 元", "id": "M002"}],
    })
    parsed = candidate_ref.parse(
        {candidate_ref.META_KEY: json.dumps(produced, ensure_ascii=False)})
    assert parsed is not None
    assert parsed["source_intent"] == "mcd.menu"
    assert [(it["name"], it["id"]) for it in parsed["items"]] == [
        ("巨无霸套餐", "M001"), ("双层吉士堡套餐", "M002")]
    got = candidate_ref.resolve("第二个",
                                {candidate_ref.META_KEY: json.dumps(produced)},
                                namespace="mcd")
    assert got is not None and got == {
        "index": 2, "name": "双层吉士堡套餐", "id": "M002"}


# ── F. 声明面：candidate_slot 必须是这个 workflow 真有的槽 ────────────
def test_declared_candidate_slot_exists_in_the_workflow_slots():
    """声明一个 planner 永远填不到的槽 = 没声明。

    同 B4 那条「校验要复刻消费方的解析」——这里复刻的是 `_resolve_candidate_slot`
    实际去读的那个键。
    """
    declared = 0
    for server in load_servers("agents/mcp_bridge/servers.yaml"):
        for spec in server.workflows:
            if not spec.candidate_slot:
                continue
            declared += 1
            assert spec.candidate_slot in spec.slots, (
                f"{spec.intent} 声明了 candidate_slot={spec.candidate_slot}，"
                f"但它不在 slots 里")
    assert declared >= 4, "两家商户的 order/menu 四条都该声明"


# ── G. 挂点：handle 真的调了它，workflow 收到的是翻译后的槽值 ─────────
class _FakeWorkflow:
    def __init__(self):
        self.seen: list[tuple[str, dict]] = []

    async def _record(self, stage, intent):
        self.seen.append((stage, dict(intent.slots)))
        return SimpleNamespace(status="ok", speech="", ui_card=None,
                               actions=[], follow_up="", data={},
                               missing_slots=[], error="")

    async def prepare(self, intent, ctx, meta):
        return await self._record("prepare", intent)

    async def menu(self, intent, ctx, meta):
        return await self._record("menu", intent)

    async def confirm(self, intent, ctx, meta, token=""):
        return await self._record("confirm", intent)

    async def cancel(self, intent, ctx, meta):
        return await self._record("cancel", intent)


async def _bridge_with(intent_name, *, candidate_slot="item_query"):
    agent = McpBridgeAgent()
    await agent.bootstrap()
    workflow = _FakeWorkflow()
    agent._workflow_bindings[intent_name] = SimpleNamespace(
        spec=SimpleNamespace(
            intent=intent_name, required_scopes=["merchant.read"],
            slots=["item_query"], candidate_slot=candidate_slot),
        workflow=workflow)
    return agent, workflow


_GRANTED = "merchant.read,merchant.write"


@pytest.mark.asyncio
@pytest.mark.parametrize("intent_name,stage", [
    ("mcd.order", "prepare"),
    ("mcd.menu", "menu"),
])
async def test_handle_translates_before_dispatching(intent_name, stage):
    agent, workflow = await _bridge_with(intent_name)
    try:
        await run_handle(agent, intent_name, {"item_query": "第一杯"},
                         ctx=make_context(),
                         meta=_meta(granted_scopes=_GRANTED))
    finally:
        await agent.shutdown()
    assert workflow.seen == [(stage, {"item_query": "北非蛋风味麦满分套餐"})]


@pytest.mark.asyncio
async def test_confirmation_path_is_not_translated():
    """确认轮的商品在草稿里早已核价定死；这里改槽值只会与草稿对不上。"""
    agent, workflow = await _bridge_with("mcd.order")
    try:
        await run_handle(agent, "mcd.order",
                         {"item_query": "第一杯", "checkout_token": "t"},
                         meta=_meta(granted_scopes=_GRANTED, confirmed="true"))
    finally:
        await agent.shutdown()
    assert workflow.seen == [("confirm", {"item_query": "第一杯",
                                          "checkout_token": "t"})]


@pytest.mark.asyncio
async def test_cancel_path_is_not_translated():
    agent, workflow = await _bridge_with("mcd.order_cancel")
    try:
        await run_handle(agent, "mcd.order_cancel", {"item_query": "第一杯"},
                         meta=_meta(granted_scopes=_GRANTED))
    finally:
        await agent.shutdown()
    assert workflow.seen == [("cancel", {"item_query": "第一杯"})]


@pytest.mark.asyncio
async def test_workflow_without_declaration_is_untouched():
    """没声明 `candidate_slot` 的 workflow 一个字都不改——加一家商户=改表。"""
    agent, workflow = await _bridge_with("mcd.order", candidate_slot="")
    try:
        await run_handle(agent, "mcd.order", {"item_query": "第一杯"},
                         meta=_meta(granted_scopes=_GRANTED))
    finally:
        await agent.shutdown()
    assert workflow.seen == [("prepare", {"item_query": "第一杯"})]


@pytest.mark.asyncio
async def test_no_downlink_means_no_change():
    """没有候选集下发（未声明 context_scope / 上一轮没出过卡）= 行为逐字同今天。"""
    agent, workflow = await _bridge_with("mcd.order")
    try:
        await run_handle(agent, "mcd.order", {"item_query": "第一杯"},
                         meta={"granted_scopes": _GRANTED})
    finally:
        await agent.shutdown()
    assert workflow.seen == [("prepare", {"item_query": "第一杯"})]


# ── H. 三种形态：真栈同一句话，planner 填成三个样子 ──────────────────
#
# 「麦当劳的第七个多少钱」三次取样实测（cloud 档 870f4bc）：
#   ① `item_query="第七个"`  ② **`category`**`="第7个"`  ③ **一个槽都没填**
# 首版只做了 ①，读数 1/3。⇒ 判据的完整版是：不只「序数落到哪一项」不该让 LLM 数，
# **「序数该放进哪个槽」也不该由它决定**。

def test_a_bare_ordinal_is_recognized_wherever_it_lands():
    for raw in ["第七个", "第7个", "第 2 杯", "第三", "那个第一个", "第十款"]:
        assert candidate_ref.is_bare_ordinal(raw), raw
    for raw in ["巨无霸套餐", "生椰拿铁第二杯半价", "汉堡", "", "第一个和第二个"]:
        assert not candidate_ref.is_bare_ordinal(raw), raw


@pytest.mark.parametrize("raw_text,expected", [
    ("麦当劳的第七个多少钱", None),                    # 越界（fixture 只有 3 项）
    ("麦当劳的第二个多少钱", "猪柳蛋麦满分套餐"),
    ("那第一个呢", "北非蛋风味麦满分套餐"),
    ("第一个和第二个一共多少钱", None),                # 聚合问句，归云侧
    ("看看麦当劳有什么可以点的", None),                # 没有序数
    ("", None),
])
def test_raw_text_channel_is_the_last_resort(raw_text, expected):
    got = candidate_ref.from_raw_text(raw_text, _meta(), namespace="mcd")
    assert (got or {}).get("name") == expected, raw_text


@pytest.mark.asyncio
async def test_ordinal_in_the_wrong_slot_is_redirected_and_cleared():
    """真栈取样 ②：planner 把「第7个」填进 `category`。

    **必须把错位那个槽清掉**——留着它，商户侧会先按「有没有这个分类」过滤，
    然后诚实查无（真栈原话「餐单里没有「第7个」这一类」），翻译等于白做。
    """
    agent, workflow = await _bridge_with("mcd.menu")
    agent._workflow_bindings["mcd.menu"].spec.slots = ["item_query", "category"]
    try:
        await run_handle(agent, "mcd.menu", {"category": "第7个"},
                         raw_text="麦当劳的第七个多少钱",
                         meta=_meta(items=[{"index": i, "name": f"第{i}款"}
                                           for i in range(1, 9)],
                                    granted_scopes=_GRANTED))
    finally:
        await agent.shutdown()
    assert workflow.seen == [("menu", {"item_query": "第7款"})]


@pytest.mark.asyncio
async def test_no_slot_at_all_falls_back_to_the_raw_text():
    """真栈取样 ③：planner 一个槽都没填，整份菜单原样又出一遍。"""
    agent, workflow = await _bridge_with("mcd.menu")
    try:
        await run_handle(agent, "mcd.menu", {},
                         raw_text="麦当劳的第二个多少钱",
                         meta=_meta(granted_scopes=_GRANTED))
    finally:
        await agent.shutdown()
    assert workflow.seen == [("menu", {"item_query": "猪柳蛋麦满分套餐"})]


@pytest.mark.asyncio
async def test_an_explicit_value_is_never_overwritten_by_the_fallbacks():
    """②③ 只在目标槽为空时才动手——用户说出口的实质内容永远优先。"""
    agent, workflow = await _bridge_with("mcd.menu")
    agent._workflow_bindings["mcd.menu"].spec.slots = ["item_query", "category"]
    try:
        await run_handle(agent, "mcd.menu",
                         {"item_query": "拿铁", "category": "第2个"},
                         raw_text="麦当劳的第二个拿铁多少钱",
                         meta=_meta(granted_scopes=_GRANTED))
    finally:
        await agent.shutdown()
    # 「拿铁」在本 fixture 里一项都不命中 ⇒ 原样不动，category 也不清。
    assert workflow.seen == [("menu", {"item_query": "拿铁",
                                       "category": "第2个"})]


@pytest.mark.asyncio
async def test_raw_ordinal_disambiguates_duplicate_names_and_carries_trusted_id():
    """MC2：模型把「第二个」改写成同名商品时，原话序数 + 商品码仍能唯一落项。"""
    duplicate = [
        {"index": 1, "name": "猪柳蛋麦满分套餐", "id": "BREAKFAST-A"},
        {"index": 2, "name": "猪柳蛋麦满分套餐", "id": "BREAKFAST-B"},
    ]
    agent, workflow = await _bridge_with("mcd.order")
    try:
        await run_handle(
            agent, "mcd.order", {"item_query": "猪柳蛋麦满分套餐"},
            raw_text="在麦当劳点第二个",
            meta=_meta(items=duplicate, granted_scopes=_GRANTED),
        )
    finally:
        await agent.shutdown()
    assert workflow.seen == [("prepare", {
        "item_query": "猪柳蛋麦满分套餐",
        "_candidate_ref_id": "BREAKFAST-B",
    })]


@pytest.mark.asyncio
async def test_planner_cannot_inject_the_reserved_candidate_identity():
    """商品码只能来自服务端候选投影；模型伪造的保留槽必须先被清掉。"""
    agent, workflow = await _bridge_with("mcd.order")
    try:
        await run_handle(
            agent, "mcd.order",
            {"item_query": "巨无霸套餐", "_candidate_ref_id": "FORGED"},
            raw_text="点一份巨无霸套餐",
            meta={"granted_scopes": _GRANTED},
        )
    finally:
        await agent.shutdown()
    assert workflow.seen == [("prepare", {"item_query": "巨无霸套餐"})]
