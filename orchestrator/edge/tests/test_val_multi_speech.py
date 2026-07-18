"""多意图车控反馈语（TTS 播报优化批次）：批次成员话术选名词式全句。

单意图默认 brief（「开了」）不变；multi=True 时强制名词式 full——简短应答在合并播报里
无法归属（「开了，好的」是谁开了？），礼貌式（「已为您打开空调，已为您打开车窗」）堆叠
重复冗长。名词式合并读作「空调已开启，天窗已打开」。
"""
from val import VAL


def _mk(obj, operate="open", value=None):
    data = {"object": obj, "operate": operate}
    if value is not None:
        data["value"] = value
    return {"domain": "car_control", "intent": f"{obj}.{operate}", "data": data}


def test_multi_uses_noun_first_full_speech():
    val = VAL()
    ok, speech = val.execute(_mk("aircon"), multi=True)
    assert ok
    assert speech == "空调已开启"          # 名词式 full，非「开了」也非「已为您打开空调」


def test_multi_is_deterministic():
    val = VAL()
    outs = {val.execute(_mk("sunroof"), multi=True)[1] for _ in range(8)}
    assert outs == {"天窗已打开"}          # 去随机：合并播报风格稳定


def test_multi_joined_reads_naturally():
    val = VAL()
    speeches = [val.execute(_mk("aircon"), multi=True)[1],
                val.execute(_mk("sunroof"), multi=True)[1]]
    assert "，".join(speeches) == "空调已开启，天窗已打开"
    assert "已为您" not in "，".join(speeches)


def test_multi_falls_back_when_only_courtesy_variant():
    """只有礼貌式变体的模板（如 air_purifier）：退回该变体，不至于无话可播。"""
    val = VAL()
    ok, speech = val.execute(_mk("air_purifier"), multi=True)
    assert ok and speech == "已为您打开空气净化"


def test_single_intent_brief_behavior_unchanged():
    val = VAL()
    ok, speech = val.execute(_mk("aircon"))
    assert ok and speech in ("开了", "好的")   # 默认 short → brief（原行为）
