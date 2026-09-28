"""Preserve a producer's guarded answer, including the roles of existing numbers."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.models import StepResult, StepStatus


def protected_answer(**overrides):
    values = dict(step_id="manual", status=StepStatus.OK,
                  speech="推荐冷态胎压是 2.9 bar；低于 2.3 bar 时报警。",
                  data={"_speech_verbatim":True}, ui_card={"type":"evidence-card"})
    values.update(overrides)
    return StepResult(**values)


def test_existing_numbers_cannot_swap_meaning_during_multi_result_composition():
    llm=AsyncMock(return_value="推荐冷态胎压是 2.3 bar；低于 2.9 bar 时报警。")
    safety=StepResult("safety",StepStatus.OK,speech="请减速并就近检查。")
    weather=StepResult("weather",StepStatus.OK,speech="气温 25 度。",ui_card={"type":"weather"})
    answer=protected_answer()
    out=asyncio.run(Aggregator(llm).compose("胎压补到多少，也查一下天气",[safety,answer,weather]))
    assert out["speech"] == "请减速并就近检查。\n\n" + answer.speech + "\n\n气温 25 度。"
    assert out["ui_card"] == {"type":"card_group","items":[answer.ui_card,weather.ui_card]}
    assert out["actions"] == []
    llm.assert_not_awaited()


def test_protected_composition_keeps_failure_refusal_followup_and_verifier_note():
    llm=AsyncMock(return_value="everything succeeded")
    answer=protected_answer(data={"_speech_verbatim":True,"_verify":{"mode":"schema","verdict":"unsat"}})
    refused=StepResult("refused",StepStatus.OK,speech="这项服务不支持。",follow_up="可以换一个时间。",data={"_refused":True})
    failed=StepResult("failed",StepStatus.FAILED,error="step_timeout")
    out=asyncio.run(Aggregator(llm).compose("继续",[answer,refused,failed]))
    assert answer.speech in out["speech"]
    assert out["speech"].count("这项服务不支持。") == 1
    assert "处理超时" in out["speech"] and "没拿到实际内容" in out["speech"]
    assert out["follow_up"]==refused.follow_up
    llm.assert_not_awaited()


@pytest.mark.parametrize("flag", [False, "true", 1, None])
def test_only_a_boolean_producer_contract_changes_normal_composition(flag):
    llm=AsyncMock(return_value="普通合成")
    answer=protected_answer(data={"_speech_verbatim":flag})
    other=StepResult("other",StepStatus.OK,speech="另一项回答。")
    out=asyncio.run(Aggregator(llm).compose("问题",[answer,other]))
    assert out["speech"]=="普通合成"
    llm.assert_awaited_once()


def test_an_empty_history_reference_does_not_disable_new_result_composition():
    llm=AsyncMock(return_value="只合成新结果")
    past=protected_answer(speech="",from_history=True)
    fresh=[StepResult("one",StepStatus.OK,speech="新结果一。"),StepResult("two",StepStatus.OK,speech="新结果二。")]
    out=asyncio.run(Aggregator(llm).compose("继续",[past,*fresh]))
    assert out["speech"]=="只合成新结果"
    llm.assert_awaited_once()


def test_protected_answer_does_not_change_existing_action_composition():
    llm=AsyncMock(return_value="wrong")
    action={"type":"navigate","payload":{"destination":"目的地"},"require_confirm":False}
    other=StepResult("navigation",StepStatus.OK,speech="已规划路线。",actions=[action])
    out=asyncio.run(Aggregator(llm).compose("问题",[protected_answer(),other]))
    assert out["actions"]==[action]
    llm.assert_not_awaited()


def test_producer_contract_survives_sdk_wire_and_executor_conversion():
    from agents._sdk.result import AgentResult
    from agents._sdk.server import _result_to_proto
    from agents.manual_rag.src.agent import ManualRagAgent
    from cockpit.agent.v1 import agent_pb2
    from orchestrator.cloud.executor import DagExecutor

    producer=AgentResult(speech="推荐冷态胎压 2.9 bar；报警阈值为 2.3 bar。",
                         data=ManualRagAgent._safety_data("", "胎压多少", source_type="manual"))
    response=agent_pb2.ExecuteResponse.FromString(_result_to_proto(producer).SerializeToString())
    received=DagExecutor._to_result("evidence",response)
    assert received.data["_speech_verbatim"] is True
    llm=AsyncMock(return_value="推荐 2.3 bar")
    out=asyncio.run(Aggregator(llm).compose("怎么处理",[received,StepResult("other",StepStatus.OK,speech="请就近检查。")]))
    assert producer.speech in out["speech"]
    llm.assert_not_awaited()
