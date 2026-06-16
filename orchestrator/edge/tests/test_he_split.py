"""端侧“X和Y”安全二次拆分：仅 local 车控并列才拆，否则整段保留。"""
from fast_intent import split_and_classify, split_and_classify_any


def test_seat_heating_and_ventilation_split():
    r = split_and_classify_any("座椅加热和座椅通风安排上")
    assert r is not None
    modes = sorted(i["data"].get("mode") for i in r)
    assert modes == ["heating", "ventilation"]


def test_local_controls_joined_by_he_split():
    r = split_and_classify("空调打开和氛围灯打开")
    # 两个本地车控并列 → 拆成 2 个意图
    assert r is not None and len(r) == 2


def test_he_not_split_when_non_local():
    # 导航是云端意图 → “X和Y”不拆，整句上云（保语义）
    assert split_and_classify("导航去北京和上海") is None


def test_he_not_split_when_unclassifiable():
    # 非车控/无法本地识别 → 不拆，避免误拆人名/词组
    assert split_and_classify_any("我和你聊天") is None
