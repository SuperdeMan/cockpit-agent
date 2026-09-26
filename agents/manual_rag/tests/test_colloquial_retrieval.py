"""车主口语问法的手册召回（2026-09-26，collector 真实问法驱动）。

生产 collector 562 轮手册问答里 488 轮「手册里没有查到」，高频的正是「座椅加热有几个档位」
「空调有哪些模式」「车窗有哪些开启方式」「后备箱能放几个行李箱」——正确页排序第一，却被
覆盖率闸拦下：问句壳切出的「有几 / 几个 / 个档」手册里一次都没有、IDF 最高。

这里用合成小手册钉住查询理解的每一条规则，以及它们不许放宽的另一面：别的车型、本车没有的
对象、手册没有的型号码照旧零命中。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from agents.manual_rag.src.index_format import (
    ExtractedPage,
    build_index_bundle,
    load_manual_package,
    write_index_bundle,
)
from agents.manual_rag.src.providers.local_index import (
    ManualIndexError,
    ManualIndexRetriever,
)

_RESOURCES = Path(__file__).resolve().parents[1] / "resources"

_PAGES = [
    ExtractedPage(20, ("开启和关闭", "车门和车窗", "车窗控制"),
                  "车窗控制。按键开启或关闭车窗：手动模式下按下或拉起车窗开关至第一个挡位，"
                  "车窗停在松开的位置；自动模式下拉起至第二个挡位后松开，车窗自动开启或关闭。"),
    ExtractedPage(29, ("座椅和安全", "座椅", "主驾座椅调节"),
                  "主驾座椅调节。通过座椅左侧的调节开关调节座椅前后位置、座椅高度、"
                  "靠背倾斜角度和腰部支撑。"),
    ExtractedPage(37, ("座椅和安全", "座椅", "座椅加热"),
                  "座椅加热。在中控屏打开空调控制中心>座椅，点击座椅加热开关，切换加热强度"
                  "（3-2-1-off），也可设为自动挡位，由系统调节加热挡位。"),
    ExtractedPage(38, ("座椅和安全", "座椅 > 座椅通风 / 车辆安全 > 安全带"),
                  "座椅通风。点击座椅通风开关切换通风强度。安全带。驾乘人员须正确佩戴安全带。"),
    ExtractedPage(53, ("座椅和安全", "车辆安全", "哨兵模式"),
                  "哨兵模式。开启后车辆停放期间通过摄像头监测周围环境，并录制视频。"),
    ExtractedPage(87, ("驾驶和操作", "灯光", "远近光灯"),
                  "外灯设置。可选择关闭、位置灯、近光灯、自动四个外灯挡位，车辆大灯会根据"
                  "环境光线自动切换。"),
    ExtractedPage(106, ("储物和车内装备", "储物", "前备箱储物"),
                  "前备箱储物。开启前备箱盖后可装载物品，前备箱中可容纳 20 寸的行李箱。"),
    ExtractedPage(107, ("储物和车内装备", "储物", "后备箱储物"),
                  "后备箱储物。后备箱储物空间分为隔板上储物空间和隔板下储物空间，"
                  "若有更大储物需求，可将后排座椅放倒。"),
    ExtractedPage(193, ("信息显示和娱乐", "中控显示屏", "警告灯和指示灯"),
                  "警告灯和指示灯。安全带未系提醒指示灯。安全气囊故障指示灯。胎压监测报警指示灯。"),
    ExtractedPage(197, ("信息显示和娱乐", "空调控制", "空调控制"),
                  "空调控制界面：吹风模式、风量调节、空气净化功能、空调自动模式、"
                  "空调制冷/制热模式（A/C）、空气内/外循环。"),
    ExtractedPage(243, ("车辆规格", "规格与参数", "整车尺寸参数"),
                  "整车尺寸参数。长度 4997 mm，宽度 1963 mm，轴距 3000 mm。"),
    ExtractedPage(247, ("车辆规格", "规格与参数", "动力电池参数"),
                  "动力电池参数。储能装置种类：磷酸铁锂电池。储能装置总储电量（kWh）：73.6。"
                  "续航里程（km）：700。"),
]


# 真实手册 269 页：问句壳切出的「有几 / 个档」全书不存在，IDF≈6.3，而主题词多在 1.5–4 之间，
# 正是这个偏斜把正确页压到闸下。十几页的小语料里缺席双字 IDF 只有 3 出头，复现不了
# 偏斜——去掉问句壳剥离测试照样绿。填充页只放手册里最常见的操作套话，不碰被测主题。
_FILLER_TOPICS = ("遮阳板", "化妆镜", "挂钩", "顶衬拉手", "阅读灯", "脚部照明灯", "车门外把手灯",
                  "喇叭", "转向灯", "位置灯", "后雾灯", "驾驶声音", "行车记录", "智驾学堂",
                  "路面感知系统", "紧急车道保持", "超速告警", "低速防撞预警", "泊车影像",
                  "智驾休眠", "拨杆变道辅助", "远程信息查询", "主界面", "控制中心", "应用中心",
                  "个人中心", "系统更新", "一键隐私", "恢复出厂设置", "电话", "多媒体", "智能语音",
                  "应急解锁充电枪", "设置警示牌", "随车工具", "紧急呼叫", "道路救援", "车内应急开门",
                  "车辆高压下电", "牵引事故车辆", "车辆涉水救援", "诊断接口", "驱动电机识别标志",
                  "推荐的油液和容量", "单位术语", "外部清洁保养", "内部清洁保养", "质量担保注意事项")
_FILLER = [
    ExtractedPage(120 + i, ("附录", "其他功能", topic),
                  f"{topic}。请在中控屏下方控制栏打开设置，进入车辆控制，点击开启或关闭该功能。"
                  "说明：车辆处于 P 挡时方可操作。注意：操作前请确认周围环境安全，"
                  "如有疑问请联系小米汽车服务中心。")
    for i, topic in enumerate(_FILLER_TOPICS)
]


def _index_path(tmp_path: Path, pages=_PAGES) -> Path:
    pages = [*pages, *_FILLER]
    bundle = build_index_bundle(
        pages,
        document_id="xiaomi-su7-2024-user-manual",
        title="SU7用户手册",
        publisher="小米汽车",
        vehicle_model="xiaomi-su7-2024",
        vehicle_aliases=["SU7", "小米SU7", "Xiaomi SU7", "SU7 Pro", "SU7 Max"],
        revision="2024-04-15",
        source_file="manual.pdf",
        source_sha256="a" * 64,
        source_pages=278,
    )
    path = tmp_path / "manual.v1.json.gz"
    write_index_bundle(path, bundle)
    return path


def _catalog_path(tmp_path: Path, index_path: Path) -> Path:
    document = load_manual_package(index_path).index["document"]
    trusted = {key: document[key] for key in (
        "title", "publisher", "vehicle_model", "revision", "source_pages",
        "source_sha256", "content_sha256")}
    path = tmp_path / "manual_catalog.yaml"
    path.write_text(yaml.safe_dump(
        {"schema_version": 1, "documents": {document["document_id"]: trusted}},
        allow_unicode=True, sort_keys=True), encoding="utf-8")
    return path


def _provider(tmp_path: Path, *, config: Path | None = None, pages=_PAGES) -> ManualIndexRetriever:
    index_path = _index_path(tmp_path, pages)
    return ManualIndexRetriever(
        index_path, catalog_path=_catalog_path(tmp_path, index_path),
        retrieval_config_path=config)


def _pages(provider: ManualIndexRetriever, query: str) -> list[int]:
    return [chunk.page_start for chunk in asyncio.run(provider.retrieve(query))]


# ── 问句壳：计数 / 列举 / 位置 / 定义问不再拖垮覆盖率 ────────────────────────

@pytest.mark.parametrize("query", [
    "座椅加热有几个档位",      # 计数壳 + 档/挡 异体（手册写「挡」）
    "座椅有哪些加热档位",
    "座椅加热在哪里打开",      # 位置问
    "座椅加热是什么",          # 定义问
])
def test_question_shell_does_not_sink_the_right_page(tmp_path, query):
    assert _pages(_provider(tmp_path), query)[:1] == [37]


def test_enumeration_question_reaches_mode_list(tmp_path):
    assert _pages(_provider(tmp_path), "空调有哪些模式")[:1] == [197]


def test_answer_type_word_absent_from_manual_does_not_count_against_topic(tmp_path):
    # 手册列的是「手动模式 / 自动模式」，没有「方式」二字。
    assert _pages(_provider(tmp_path), "车窗有哪些开启方式")[:1] == [20]


def test_ba_construction_bigrams_are_syntax_not_topic(tmp_path):
    # 「把车窗打开」切出「把车 / 窗打」：一个段首介词、一个跨词接缝。
    assert _pages(_provider(tmp_path), "怎么把车窗打开")[:1] == [20]


def test_colloquial_name_reaches_manual_wording(tmp_path):
    provider = _provider(tmp_path)
    assert _pages(provider, "大灯有几个模式")[:1] == [87]
    assert _pages(provider, "驾驶座能调哪些方向")[:1] == [29]


def test_capacity_question_finds_the_storage_page(tmp_path):
    assert 106 in _pages(_provider(tmp_path), "后备箱能放几个行李箱")


# ── 整车规格：内容剥空时，只有声明了整车主语的扩展能单独成立 ────────────────

def test_vehicle_dimension_question_stands_alone(tmp_path):
    provider = _provider(tmp_path)
    assert _pages(provider, "这车有多长")[:1] == [243]
    assert _pages(provider, "车身有多长")[:1] == [243]


def test_bare_price_question_does_not_borrow_a_spec_page(tmp_path):
    provider = _provider(tmp_path)
    assert _pages(provider, "这个多少钱") == []
    assert _pages(provider, "多少") == []


def test_expansion_evidence_does_not_launder_another_brand(tmp_path):
    """扩展补的「动力电池参数 总储电量」只计排序，不计零命中闸——否则别家车型的
    电池容量问法会被洗成本车参数页。"""
    provider = _provider(tmp_path)
    assert _pages(provider, "电池容量是多少")[:1] == [247]
    assert _pages(provider, "比亚迪海豹的电池容量是多少") == []
    assert provider.scope_veto("比亚迪海豹的电池容量是多少") == "foreign_vehicle"


def test_unknown_subject_is_reported_not_silently_covered(tmp_path):
    """品牌表枚举不完（奇骏、卡罗拉…）。词法层分不开「车主叫法」与「别家车型」，但必须把
    手册不认识的实词报出来，交给目录路由判（零命中否决在 Agent 层测）。同义词换得掉的不算。"""
    provider = _provider(tmp_path)
    assert provider.scope_veto("奇骏的电池容量是多少") == ""
    assert provider.unknown_subject_terms("奇骏的电池容量是多少") == ["奇骏"]
    assert provider.unknown_subject_terms("尾箱能放几个行李箱") == []
    assert provider.unknown_subject_terms("座椅加热有几个档位") == []


# ── 查询理解的直接断言 ────────────────────────────────────────────────────

@pytest.mark.parametrize("query,content", [
    ("座椅加热有几个档位", "座椅加热 挡位"),          # 计数壳 + 档→挡
    ("后备箱能放几个行李箱", "后备箱 行李箱"),
    ("空调有哪些模式", "空调 模式"),
    ("座椅加热在哪里打开", "座椅加热 打开"),
    ("座椅加热是什么", "座椅加热"),
    ("座椅能调多高", "座椅"),                        # 属性壳
])
def test_question_shell_is_stripped_from_the_first_variant(tmp_path, query, content):
    assert _provider(tmp_path)._query_variants(query)[0].content == content


def test_question_shell_vocabulary_comes_from_the_single_authority():
    """问句壳不在检索声明里另抄一份：取自 runtime.question_shape。"""
    from agents.manual_rag.src.providers import local_index
    from runtime import question_shape
    config = yaml.safe_load((_RESOURCES / "retrieval.yaml").read_text(encoding="utf-8"))
    duplicated = set(config["query_noise_phrases"]) & set(question_shape.ENUMERATION_ASKS
                                                          + question_shape.DEFINITION_ASKS)
    assert not duplicated
    assert "有哪些" in local_index._SHELL_PHRASES and "是什么" in local_index._SHELL_PHRASES


# ── 范围闸：照旧零命中的另一面 ────────────────────────────────────────────

@pytest.mark.parametrize("query,reason", [
    ("理想L9的空悬能调几档", "unknown_term"),     # 字母+数字型号码
    ("问界M9的轴距是多少", "foreign_vehicle"),
    ("油箱能加多少升油", "absent_subject"),        # 纯电车没有油箱
    ("这车能加95号汽油吗", "absent_subject"),
    ("天窗为什么关不上", "absent_subject"),
    ("SU7 支持 CarPlay 吗", "unknown_term"),
])
def test_out_of_scope_questions_stay_zero_hit(tmp_path, query, reason):
    provider = _provider(tmp_path)
    assert _pages(provider, query) == []
    assert provider.scope_veto(query) == reason


def test_symptom_phrase_alone_does_not_match_unrelated_page(tmp_path):
    # 「关不上」是答案类型（故障），主题是「天窗」——手册没有天窗就不能凭「不上」命中。
    assert _pages(_provider(tmp_path), "天窗为什么关不上") == []


def test_alias_can_rescue_an_ascii_name_the_gate_would_block(tmp_path):
    pages = [*_PAGES, ExtractedPage(
        114, ("储物和车内装备", "车内装备", "USB接口"), "USB接口。前排与后排均配有 USB接口。")]
    provider = _provider(tmp_path, pages=pages)
    assert provider.scope_veto("后排有没有TypeC的插口") == ""
    assert _pages(provider, "后排有没有TypeC的插口")[:1] == [114]


def test_routing_is_blocked_for_icon_questions_and_scope_vetoes(tmp_path):
    provider = _provider(tmp_path)
    assert provider.route_block_reason("仪表上黄色感叹号是什么意思") == "visual_context"
    assert provider.route_block_reason("比亚迪的续航多少") == "foreign_vehicle"
    assert provider.route_block_reason("停车时有人刮车能录下来吗") == ""
    assert provider.route_block_reason("这是什么") == "no_content"
    assert provider.route_block_reason("怎么回事") == "no_content"
    # 单字实词进不了双字表，但它是内容：「挡」要交给路由去找「挡位」一节。
    assert provider.route_block_reason("这车都有哪些挡") == ""


def test_declared_marker_that_appears_in_the_manual_rejects_startup(tmp_path):
    """「本车没有」是对手册的断言：手册里其实写着它，声明就会把真实内容挡成零命中。"""
    config = yaml.safe_load((_RESOURCES / "retrieval.yaml").read_text(encoding="utf-8"))
    config["absent_subject_markers"] = [*config["absent_subject_markers"], "哨兵"]
    path = tmp_path / "retrieval.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ManualIndexError, match="哨兵"):
        _provider(tmp_path, config=path)


def test_standalone_expansion_without_subject_constraint_is_rejected(tmp_path):
    config = yaml.safe_load((_RESOURCES / "retrieval.yaml").read_text(encoding="utf-8"))
    config["intent_expansions"].append({"when_any": ["多少"], "append": ["参数"],
                                        "standalone": True})
    path = tmp_path / "retrieval.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ManualIndexError, match="standalone"):
        _provider(tmp_path, config=path)


def test_v1_retrieval_config_is_rejected(tmp_path):
    """v2 改了意图扩展的语义（证据不计闸）；旧配置不能被静默按新语义读。"""
    config = yaml.safe_load((_RESOURCES / "retrieval.yaml").read_text(encoding="utf-8"))
    config["schema_version"] = 1
    path = tmp_path / "retrieval.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ManualIndexError, match="schema_version"):
        _provider(tmp_path, config=path)


# ── 目录与按章节取页 ──────────────────────────────────────────────────────

def test_table_of_contents_expands_merged_section_paths(tmp_path):
    labels = [entry.label for entry in _provider(tmp_path).table_of_contents()]
    assert "座椅和安全 > 座椅 > 座椅通风" in labels
    assert "座椅和安全 > 车辆安全 > 安全带" in labels
    assert len(labels) == len(set(labels))


def test_retrieve_sections_gives_every_picked_section_a_page(tmp_path):
    provider = _provider(tmp_path)
    by_label = {entry.label: entry.entry_id for entry in provider.table_of_contents()}
    picked = [by_label["座椅和安全 > 车辆安全 > 哨兵模式"],
              by_label["储物和车内装备 > 储物 > 后备箱储物"],
              by_label["信息显示和娱乐 > 空调控制 > 空调控制"]]
    chunks = asyncio.run(provider.retrieve_sections("停车时有人刮车能录下来吗", picked))
    pages = [chunk.page_start for chunk in chunks]
    assert {53, 107, 197} <= set(pages)
    assert pages[0] == 53                       # 路由给出的章节顺序保留
    assert all(chunk.coverage == 0.0 for chunk in chunks)   # 不是词法证据
    assert asyncio.run(provider.retrieve_sections("停车监控", ["T999"])) == []


# ── 仪表灯语境的一句多问：后半问承接主语（2026-09-26 二批）────────────────────

_TIRE_PAGES = [
    # 「还能继续开吗」自己的词在这页全在场（真实手册 p80）：承接出来的页必须提到主语。
    ExtractedPage(80, ("驾驶和操作", "车辆驾驶", "电动尾翼"),
                  "电动尾翼。车速高于 120 km/h 时尾翼自动升起，低速时收回；也能继续开启手动模式。"),
    # 「仪表盘」是显示位置，不是灯的名字：不能拿它当主语去承接。
    ExtractedPage(194, ("信息显示和娱乐", "仪表显示", "仪表盘"),
                  "仪表盘。仪表盘显示车速、挡位与续航里程，行驶中还能查看驾驶辅助状态。"),
    ExtractedPage(245, ("车辆规格", "规格与参数", "车轮与轮胎参数"),
                  "车轮与轮胎参数。轮胎规格 245/45 R19。冷态胎压：前轮 230 kPa，后轮 250 kPa。"),
    ExtractedPage(257, ("保修和保养", "车辆保养", "轮胎检查与保养"),
                  "轮胎检查与保养。胎压监测报警指示灯点亮时，请降低车速，避免急转弯和急刹车，"
                  "尽快检查胎压。"),
]


@pytest.mark.parametrize("query", [
    "胎压黄灯亮了，还能继续开吗？应该补到多少？",
    "胎压灯亮了，要打多少气",
    "胎压灯亮了应该补到多少",    # 没有逗号：灯态词之后另有主题词，也是另一问（规划器的槽常是这个形态）
    "胎压黄灯亮应该补到多少",    # 灯态只有「灯亮」
])
def test_lamp_follow_up_question_carries_the_subject(tmp_path, monkeypatch, query):
    """「…亮了」不交给目录路由（认图标只认视觉目录），整句排序又被告警词拉向指示灯页：
    后半问按「胎压应该补到多少」查才取得到规格页；承接出来的页必须提到主语，整句首页不变。"""
    provider = _provider(tmp_path, pages=[*_PAGES, *_TIRE_PAGES])
    assert provider.route_block_reason(query) == "visual_context"
    pages = _pages(provider, query)
    assert 245 in pages and 80 not in pages
    monkeypatch.setattr(provider, "_carried_rankings", lambda _query: [])
    whole = _pages(provider, query)
    assert 245 not in whole
    assert pages[:len(whole[:1])] == whole[:1]


@pytest.mark.parametrize("query", [
    "空调有哪些模式，座椅加热在哪打开",   # 不在仪表灯语境：一句多问归目录路由
    "仪表盘亮了个红灯，还能开吗",         # 主语是显示位置：灯没点名，认图标只认视觉目录
    "胎压灯亮了",                          # 没有后续问句
    "胎压灯亮了怎么办",                    # 灯态之后没有主题词：问的就是这盏灯
])
def test_subject_is_carried_only_into_named_lamp_follow_ups(tmp_path, query):
    provider = _provider(tmp_path, pages=[*_PAGES, *_TIRE_PAGES])
    assert provider._carried_rankings(query) == []
