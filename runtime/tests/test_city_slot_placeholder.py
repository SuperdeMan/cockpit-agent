"""city 槽占位值归零（runtime/slots.py，2026-09-16 真栈形态）。

规划模型把「用当前位置」写成 `city: "当前位置"`，消费方优先信槽 ⇒ 占位值被当城市名去查和风 ⇒ 400 ⇒
「没查到「当前位置」的天气」，而 meta 里明明带着坐标。判据只在唯一的归一入口：占位值 = 没给城市。
"""
import pytest

from runtime.slots import normalize_city_slot


@pytest.mark.parametrize("value", [
    "当前位置", "当前城市", "我的位置", "这里", "这儿", "本地", "当地", "附近", "未知", "不详",
    " 当前位置 ", "here", "Current Location", "my location", "unknown", "none", "null", "N/A",
])
def test_placeholder_city_values_normalize_to_empty(value):
    assert normalize_city_slot(value) == ""


@pytest.mark.parametrize("value", ["深圳", "北京市朝阳区", "杭州 ", "113.941200,22.541000", "Shenzhen", "本溪", "无锡"])
def test_real_city_names_and_coordinate_labels_pass_through(value):
    # 「本溪」「无锡」以「本」「无」开头但不是占位词——判据是整词相等，不是前缀
    assert normalize_city_slot(value) == value.strip()


def test_placeholder_inside_object_or_json_forms_is_also_rejected():
    assert normalize_city_slot({"city": "当前位置"}) == ""
    assert normalize_city_slot('{"city":"这里"}') == ""
    assert normalize_city_slot({"city": "深圳"}) == "深圳"
    assert normalize_city_slot('{"city":"深圳"}') == "深圳"


def test_existing_malformed_shapes_still_fail_closed():
    assert normalize_city_slot({"city": 123}) == ""
    assert normalize_city_slot('{"city":123}') == ""
    assert normalize_city_slot("{oops}") == ""
    assert normalize_city_slot(None) == ""
