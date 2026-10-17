"""兜底列表的项不绑给后续步（2026-10-17，demo-dqt1fu 登记项「`slot_refs` 绑兜底列表的第一项」）。

`_fallback` 同时表示两件事：用户说了的词没拿去搜（「不辣」「麦当劳」被换成「美食」），与类目是按天气 / 饮食信号猜的。前一种由产生方另外
声明 `_dropped_terms`：那份列表不是用户要的，它的第 N 项就不是他要去 / 要点的那一家，执行器不把它填给后续步；后一种照绑（用户把挑选交给了
系统）。用户这句话自己点了第几个（「…导航去第一家」）时照绑——位置是他亲手挑的。
"""
from __future__ import annotations

from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import PlanContext, Step, StepResult, StepStatus

_ITEMS = [{"id": "poi-1", "name": "微风小院·烤肉(科技园店)"}, {"id": "poi-2", "name": "樟荣·沙县小吃(南山店)"}]


def _listing(**extra) -> dict:
    return {"s1": StepResult(step_id="s1", status=StepStatus.OK, source_intent="nearby.search",
                             data={"items": list(_ITEMS), "_fallback": True, **extra})}


def _resolve(step: Step, done: dict, text: str) -> Step:
    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(step, done, PlanContext(raw_text=text))
    return step


def test_an_item_of_a_listing_that_dropped_the_users_words_is_not_bound():
    step = _resolve(Step(id="s2", agent_id="navigation", intent="navigation.navigate_to",
                         slot_refs={"destination": "s1.data.items.0.name"}),
                    _listing(_dropped_terms=["不辣"]), "导航去附近不辣的餐厅")
    assert "destination" not in step.slots


def test_a_placeholder_into_such_a_listing_is_dropped_not_sent_literally():
    step = _resolve(Step(id="s2", agent_id="navigation", intent="navigation.navigate_to",
                         slots={"destination": "${s1.data.items.0.name}", "mode": "驾车"}),
                    _listing(_dropped_terms=["不辣"]), "导航去附近不辣的餐厅")
    assert step.slots == {"mode": "驾车"}


def test_a_guessed_category_listing_still_binds():
    """按天气 / 饮食信号猜的类目（`_fallback` 但没有 `_dropped_terms`）：用户把挑选交给了系统，第一项照绑。"""
    step = _resolve(Step(id="s2", agent_id="navigation", intent="navigation.navigate_to",
                         slot_refs={"destination": "s1.data.items.0.name"}),
                    _listing(), "今天天气适合去哪玩，导航过去")
    assert step.slots["destination"] == "微风小院·烤肉(科技园店)"


def test_a_position_the_user_named_still_binds():
    step = _resolve(Step(id="s2", agent_id="navigation", intent="navigation.navigate_to",
                         slot_refs={"destination": "s1.data.items.1.name"}),
                    _listing(_dropped_terms=["不辣"]), "附近不辣的餐厅，导航去第二家")
    assert step.slots["destination"] == "樟荣·沙县小吃(南山店)"


def test_a_user_value_is_kept_and_non_item_refs_are_untouched():
    """规划器写了用户说的值（不是那串引用）照留；不指向列表项的引用（如 `s1.data.city`）不受影响。"""
    done = _listing(_dropped_terms=["不辣"])
    done["s1"].data["city"] = "深圳市"
    step = _resolve(Step(id="s2", agent_id="navigation", intent="navigation.navigate_to",
                         slots={"destination": "海岸城"},
                         slot_refs={"destination": "s1.data.items.0.name", "city": "s1.data.city"}),
                    done, "导航去附近不辣的餐厅")
    assert step.slots == {"destination": "海岸城", "city": "深圳市"}
