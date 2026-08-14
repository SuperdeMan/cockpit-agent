"""共享地标解析器单测：名字匹配过滤 + 候选解析（不依赖真实 LLM）。"""
import asyncio

from agents._sdk.landmark import (
    is_landmark_description, landmark_candidates, name_matches)


def test_is_landmark_description():
    assert is_landmark_description("深圳外形像笋一样的建筑")
    assert is_landmark_description("像船的建筑物")
    assert not is_landmark_description("深圳湾万象城")
    assert not is_landmark_description("首都机场")


def test_is_landmark_description_nicknames():
    """俗称族（EVA 二轮 G3）：绰号此前映射在 prompt 里、入口正则却够不到。"""
    assert is_landmark_description("带我去苏州打卡大秋裤")
    assert is_landmark_description("北京的大裤衩")
    assert is_landmark_description("广州小蛮腰")


def test_is_landmark_description_nature_features():
    """自然地物×形容共现（EVA 二轮 G3）：名词单独出现不触发，控误报。"""
    assert is_landmark_description("浦东有一个巨大的圆圆的湖")
    assert is_landmark_description("无锡那尊特别高的青铜佛像")
    assert not is_landmark_description("导航去东湖")           # 裸名词不触发
    assert not is_landmark_description("附近最大的超市")       # 形容词但非地物名词


def test_name_matches_filters_unrelated_poi():
    # 高德对俗称返回的邻近无关 POI 必须判不匹配
    assert not name_matches("华润春笋大厦", "V(东滨店)(装修中)")
    # 官方名 / 包含关系 / 公共子串算匹配
    assert name_matches("中国华润大厦", "中国华润大厦")
    assert name_matches("华润大厦", "中国华润大厦")          # 候选是结果子串
    assert name_matches("中国华润大厦", "中国华润大厦地下停车场")  # 结果是候选超集
    assert not name_matches("", "x") and not name_matches("x", "")


def test_landmark_candidates_parses_official_name_first():
    async def fake_llm(messages, **kwargs):
        # 校验系统提示要求"地图可检索的正式名"
        assert "正式" in messages[0]["content"]
        assert messages[-1] == {"role": "user", "content": "导航去像笋的建筑"}
        return '["中国华润大厦","华润春笋大厦"]'

    class _LLM:
        complete = staticmethod(fake_llm)

    out = asyncio.run(landmark_candidates(_LLM(), "导航去像笋的建筑"))
    assert out == ["中国华润大厦", "华润春笋大厦"]


def test_landmark_candidates_handles_llm_failure():
    class _LLM:
        async def complete(self, *a, **kw):
            raise RuntimeError("llm down")

    out = asyncio.run(landmark_candidates(_LLM(), "像笋的建筑"))
    assert out == []
