"""本地目的地探针改读冻结语料（冻结 P2，2026-10-10）：案例表只在语料里留一份，判定用共用实现。"""
from __future__ import annotations

import json

from scripts.probe_local_destinations import judge_turn, load_cases

#: 搬迁前探针里的 21 个目的地（CASES 20 + ASK 1）：一个都不能在搬迁中丢掉
_MIGRATED = ("大梅沙", "蛇口港", "东门", "海岸城", "会展中心", "山姆会员店", "厦门火车站", "儿童医院", "口腔医院",
             "7-11便利店", "华润万家超市", "医院", "上海外滩", "长沙橘子洲", "东莞松山湖", "小梅沙海滩", "7-11", "KFC",
             "北大医院", "301医院", "鼓浪屿")


def test_every_destination_of_the_old_case_table_is_in_the_corpus():
    says = {turn["say"] for case in load_cases() for turn in case["turns"]}
    missing = [d for d in _MIGRATED if f"去{d}要开多久" not in says]
    assert not missing, missing


def test_ids_select_a_subset():
    assert [c["id"] for c in load_cases(ids={"NV01", "NV21"})] == ["NV01", "NV21"]


def _obs(speech, card=None, actions=()):
    return {"speech": speech, "card_text": json.dumps(card or {}, ensure_ascii=False), "actions": list(actions)}


def test_judgement_keeps_the_old_probe_semantics():
    by_id = {c["id"]: c["turns"][0]["expect"] for c in load_cases()}
    # 借名附属点：该有的名字在、附属点不在
    assert judge_turn(by_id["NV01"], _obs("去大梅沙海滨公园全程约30公里")) == []
    assert judge_turn(by_id["NV01"], _obs("去大梅沙推拿馆全程约30公里"))
    # 类目通称的全程上限、城市限定外地名的全程下限
    assert judge_turn(by_id["NV08"], _obs("去深圳市儿童医院全程约12公里")) == []
    assert judge_turn(by_id["NV08"], _obs("去儿童医院全程约2100公里"))
    assert judge_turn(by_id["NV13"], _obs("去上海外滩全程约1480公里")) == []
    assert judge_turn(by_id["NV13"], _obs("去外滩全程约3公里"))
    # 外地本体与本地同名：候选第一项是厦门
    card = {"type": "poi_list", "purpose": "dest_choice", "items": [{"name": "鼓浪屿(厦门)"}, {"name": "鼓浪屿(深圳)"}]}
    assert judge_turn(by_id["NV21"], _obs("找到两处", card)) == []
    assert judge_turn(by_id["NV21"], _obs("找到两处", dict(card, items=card["items"][::-1])))


def test_any_action_fails_a_read_only_estimate():
    expect = load_cases(ids={"NV07"})[0]["turns"][0]["expect"]
    assert judge_turn(expect, _obs("去厦门站全程约584公里", actions=["navigate"]))
