"""旅程 gold 自身的离线回归：**判据的可判性不能依赖跑批当天的外部世界**。

这一层不跑真栈、不打网络，只拿合成的 `TurnOutcome` 去喂 `check_expect`——
它证的是「这条 gold 在各种真实世界状态下分别判成什么」，而真栈跑批一次只能撞见
其中一种状态，撞不见的那几种永远没人验。
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from e2e_journeys import TurnOutcome, check_expect, validate_journey  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_TARGET_B = _ROOT / "test" / "journeys" / "target_b.yaml"


def _journeys(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {j["id"]: j for j in (doc.get("journeys") or [])}


def _outcome(speech: str, card_type: str = "") -> TurnOutcome:
    out = TurnOutcome()
    out.final = {"speech": speech,
                 "ui_card": {"type": card_type} if card_type else None}
    return out


def _b3_1_rain_turn() -> dict:
    turn = _journeys(_TARGET_B)["B3-1"]["turns"][2]
    assert "下雨" in turn["say"], "B3-1 第三轮不是那句条件句了，断言选错了轮"
    return turn["expect"]


def test_b3_1_gold_is_judgeable_whether_or_not_it_actually_rains():
    """同一条 gold 在「下雨」和「不下雨」两种真实天气下都必须能判对。

    历史形态：`cards_any: [trip_itinerary]` 挂在 `any_of` **外面**，于是无条件要求出卡。
    可这句话是条件句——不下雨时**不改行程**才是正确答案，而正确答案没有卡。
    实测两跑分别撞上「珠海今天有中雨转雷阵雨」与「这几天都没有雨」，
    被同一条断言判成同一种红，指向的却是两个完全不同的结论。
    """
    expect = _b3_1_rain_turn()

    rained_and_replanned = _outcome(
        "周六有雨，已把那天换成室内的安排：珠海博物馆、长隆海洋王国室内馆。",
        card_type="trip_itinerary")
    assert check_expect(expect, rained_and_replanned, False, []) == []

    no_rain_said_so = _outcome("这几天珠海都没有雨，行程不用调整。")
    assert check_expect(expect, no_rain_said_so, False, []) == []


def test_b3_1_gold_still_catches_the_real_defect():
    """真缺陷必须照抓：**真下雨了却没改行程**。

    这是「把 gold 写宽让它变绿」与「把 gold 写对」的分界——写宽的写法这一条会漏。
    下雨却没改时，模型既说不出「室内」（分支一不过），也说不出「没有雨」（分支二不过）。
    """
    expect = _b3_1_rain_turn()

    rained_but_kept_the_beach = _outcome(
        "珠海周六有中雨转雷阵雨。已为您重新规划：上午海滨泳场，下午情侣路。"
        "确认按此调整吗？", card_type="trip_itinerary")
    fails = check_expect(expect, rained_but_kept_the_beach, False, [])
    assert fails, "下雨却没改行程必须判红"
    assert any("any_of" in f for f in fails)

    # 「室内」说到了但没出行程卡 = 只说不做，同样不算改成功
    talked_indoor_without_replanning = _outcome("下雨的话建议选室内景点。")
    assert check_expect(expect, talked_indoor_without_replanning, False, []), \
        "点名室内但没出行程卡不算改了行程"


def test_all_target_b_journeys_still_pass_schema_validation():
    """gold 改写不得顺手引入 schema 违规（`any_of` 分支键同样受 EXPECT_KEYS 约束）。"""
    for jid, journey in _journeys(_TARGET_B).items():
        assert validate_journey(journey) == [], f"{jid} schema 违规"
