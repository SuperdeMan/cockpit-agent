"""目的地候选卡续接的公共件（充电与导航共用，序数判据只有这一处）。"""
import asyncio

from agents._sdk.dest_choice import ordinal_in, ordinal_index, resolve_choice, resolve_ordinal, save_choices


class _Ctx:
    def __init__(self):
        self.kv = {}

    async def save_shared_state(self, key, value):
        self.kv[key] = value

    async def load_shared_state(self, key):
        return self.kv.get(key)


def test_ordinal_index():
    assert [ordinal_index(t) for t in ("第一个", "第2家", "两", "第三处", "第十")] == [1, 2, 2, 3, 10]
    assert [ordinal_index(t) for t in ("黄鹤楼", "第一个路口", "", "第一个吧")] == [0, 0, 0, 0]


def test_ordinal_in_finds_the_ordinal_inside_a_slot_answer():
    """补槽答案带着「换」「吧」这类词（换站续接：「换第二个」）：句中找序号；整句就是序号的与 `ordinal_index` 一致。"""
    assert [ordinal_in(t) for t in ("换第二个", "第2个吧", "就换第一家", "2个", "第三个", "2")] == [2, 2, 1, 2, 3, 2]
    # 不带「第」的汉字数字不是序号：「换一个充电站」「换一家」里的「一」
    assert [ordinal_in(t) for t in ("换一个充电站", "换一家", "两个都换", "", "黄鹤楼")] == [0, 0, 0, 0, 0]


def test_resolve_consumes_the_choice_and_passes_other_text_through():
    ctx = _Ctx()
    asyncio.run(save_choices(ctx, "k", [{"name": "武汉黄鹤楼", "address": "蛇山"}, {"name": "", "address": "x"},
                                        {"name": "小黄鹤楼餐馆"}]))
    assert ctx.kv["k"] == {"items": [{"name": "武汉黄鹤楼", "address": "蛇山"}, {"name": "小黄鹤楼餐馆", "address": ""}]}
    assert asyncio.run(resolve_ordinal(ctx, "k", "黄鹤楼")) == "黄鹤楼"      # 不是序号：原样
    assert asyncio.run(resolve_ordinal(ctx, "k", "第三个")) == "第三个"      # 越界：原样，不清
    assert asyncio.run(resolve_ordinal(ctx, "k", "第二个")) == "小黄鹤楼餐馆"
    assert ctx.kv["k"] == {}                                               # 消费即清
    assert asyncio.run(resolve_ordinal(ctx, "k", "第一个")) == "第一个"


def test_no_context_is_harmless():
    assert asyncio.run(resolve_ordinal(None, "k", "第一个")) == "第一个"
    asyncio.run(save_choices(None, "k", [{"name": "x"}]))


def test_choices_keep_coordinates_and_resolve_by_ordinal_or_name():
    ctx = _Ctx()
    items = [{"name": "武汉黄鹤楼", "address": "蛇山", "lat": 30.545, "lng": 114.302}, {"name": "小黄鹤楼餐馆", "lat": 22.6, "lng": 114.05}]
    asyncio.run(save_choices(ctx, "k", items))
    assert asyncio.run(resolve_choice(ctx, "k", "小黄鹤楼餐馆")) == {"name": "小黄鹤楼餐馆", "address": "", "lat": 22.6, "lng": 114.05}
    assert ctx.kv["k"] == {}
    asyncio.run(save_choices(ctx, "k", items))
    assert asyncio.run(resolve_choice(ctx, "k", "第一个"))["lat"] == 30.545
    assert asyncio.run(resolve_choice(ctx, "k", "别的地方")) is None

