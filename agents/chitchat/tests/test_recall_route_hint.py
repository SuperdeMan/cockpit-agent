"""问自己记过的偏好 / 家人称呼与生日钉到闲聊（2026-10-13，docs/design/2026-10-13-v2-200x5-followups.md §8）。

这类句子不带问号、不带「吗」，共享问句判据认不出是问句；复测 `4fc69b40` 里被派成 reminder.list、memory.remember 写入或判要澄清。
清单里那条整句锚定的 replace hint 只认两种句形；这里钉住它接什么、不接什么。
"""
import os
from types import SimpleNamespace

import pytest

from agents._sdk.manifest import load_manifest
from orchestrator.cloud.route_hints import RouteHintEngine

_MANIFEST = os.path.join(os.path.dirname(__file__), "..", "manifest.yaml")


def _hits(text):
    [hint] = [h for h in load_manifest(_MANIFEST).route_hints if "女儿" in h.pattern]
    agent = SimpleNamespace(manifest=SimpleNamespace(route_hints=[hint]))
    return RouteHintEngine(lambda raw, _map: []).matches_replace(text, {"chitchat": agent})


@pytest.mark.parametrize("text", [
    "我平时喜欢喝什么", "我平时喜欢喝什么咖啡", "那我最喜欢吃什么", "我喜欢听什么歌",
    "我女儿叫什么名字", "我女儿叫啥", "我老婆生日是哪天", "我妈妈生日是几号", "我儿子的名字是什么",
    "请问我老公哪天过生日",
])
def test_questions_about_what_i_told_you_go_to_chitchat(text):
    assert _hits(text)


@pytest.mark.parametrize("text", [
    "我喜欢喝拿铁", "记住，我女儿叫小满", "我女儿在哪个学校", "我老婆的手机号是多少",
    "我平时喜欢喝什么，帮我点一杯", "我们去吃什么", "我想喝点什么", "你还记得我女儿叫什么吗",
])
def test_statements_commands_and_other_questions_stay_with_the_planner(text):
    assert not _hits(text)
