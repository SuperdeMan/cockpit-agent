"""闲聊单测的公共夹具。"""
import pytest

from agents.chitchat.src.agent import ChitchatAgent


@pytest.fixture(autouse=True)
def _manual_claim_defaults_to_not_confident(monkeypatch, request):
    """问手册「有没有把握」是一次跨 Agent 调用；单测缺省当没把握，不去连注册中心。
    专测这条路径的模块设 `USES_MANUAL_CLAIM = True`，自己换内部调用客户端。"""
    if getattr(request.module, "USES_MANUAL_CLAIM", False):
        return

    async def not_confident(self, text, ctx):
        return False

    monkeypatch.setattr(ChitchatAgent, "_manual_confident", not_confident)
