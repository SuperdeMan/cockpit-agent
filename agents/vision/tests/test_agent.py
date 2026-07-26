"""vision Agent 契约测试（M4 P4）：诚实降级 / 图像不进对话链 / 能力性 pin。

两条最要紧的：
1. **没帧就说没帧**——绝不退化成纯文本问答让模型编一个「那可能是一座写字楼」。
   看图问答编出来的答案比查不到严重得多：用户没法判断真假（铁律③在视觉上的推论）。
2. **图像不进对话链**——Agent 只见 frame_id，见不到任何图像字节。
"""
from __future__ import annotations

import asyncio
import os

import pytest

from agents.vision.src.agent import VisionAgent


class _Intent:
    def __init__(self, raw_text="", slots=None):
        self.raw_text = raw_text
        self.slots = slots or {}
        self.name = "vision.describe"
        self.confidence = 1.0


def _agent(monkeypatch, answer="那是一棵香樟树。", boom=False):
    a = VisionAgent()
    calls: list[dict] = []

    async def fake_complete(messages, model="", temperature=0.7, max_tokens=512,
                            timeout=10, thinking=None, extra_meta=None):
        calls.append({"messages": messages, "model": model, "extra_meta": extra_meta or {}})
        if boom:
            raise RuntimeError("vision model down")
        return answer

    monkeypatch.setattr(a.llm, "complete", fake_complete)
    return a, calls


def test_no_frame_degrades_honestly(monkeypatch):
    """没 frame_id → 诚实说没画面，且**根本不调 LLM**（不给编造留机会）。"""
    a, calls = _agent(monkeypatch)
    r = asyncio.run(a.handle(_Intent("那是什么"), None, {}))
    assert calls == [], "没帧还去问模型 = 给编造留了机会"
    assert "没拿到画面" in r.speech
    assert r.data["_prov"]["reason"] == "no_frame"


def test_no_frame_uses_ok_not_failed(monkeypatch):
    """R9 契约：话术型拒绝用 OK——FAILED 会被聚合器吞成裸「抱歉，处理失败」。"""
    a, _ = _agent(monkeypatch)
    r = asyncio.run(a.handle(_Intent("那是什么"), None, {}))
    assert r.status.lower() == "ok", f"话术型拒绝用了 {r.status}"


def test_frame_id_is_passed_as_reference_only(monkeypatch):
    """Agent 只转 frame_id；messages 里不得出现任何图像内容。"""
    a, calls = _agent(monkeypatch)
    r = asyncio.run(a.handle(_Intent("那是什么"), None, {"vision_frame_id": "vf_abc123"}))
    assert calls[0]["extra_meta"]["vision_frame_id"] == "vf_abc123"
    blob = str(calls[0]["messages"])
    for lit in ("data:image", "base64", "image_url"):
        assert lit not in blob, "图像内容进了对话链——它只该在网关内存里"
    assert r.data["answer"] == "那是一棵香樟树。"


def test_vision_pins_its_own_provider(monkeypatch):
    """能力性 pin：看图必须打到 VL 档，不跟随用户切的聊天大脑。

    实测依据（P4b 探针）：qwen3.7-max 对多模态 content 直接 400；而档位解析对不认识的
    模型是**静默回落 primary**，不 pin 就会静默打到一个看不了图的模型上。
    """
    monkeypatch.setenv("VISION_PROVIDER", "qwen-vl")
    a, calls = _agent(monkeypatch)
    asyncio.run(a.handle(_Intent("那是什么"), None, {"vision_frame_id": "vf_x"}))
    assert calls[0]["extra_meta"]["llm_provider"] == "qwen-vl"


def test_model_failure_degrades_honestly(monkeypatch):
    """视觉模型不可用 → 说不可用，不静默回落成文本问答。"""
    a, _ = _agent(monkeypatch, boom=True)
    r = asyncio.run(a.handle(_Intent("那是什么"), None, {"vision_frame_id": "vf_x"}))
    assert "看不了图" in r.speech
    assert r.data["_prov"]["reason"] == "vision_model_unavailable"


def test_empty_answer_is_not_reported_as_success(monkeypatch):
    """模型答空 → 诚实说没看清（verification schema 也会判假完成）。"""
    a, _ = _agent(monkeypatch, answer="   ")
    r = asyncio.run(a.handle(_Intent("那是什么"), None, {"vision_frame_id": "vf_x"}))
    assert "没看清" in r.speech
    assert "answer" not in (r.data or {})


def test_card_marks_simulated_camera(monkeypatch):
    """PoC 没有真车外摄像头（画面来自浏览器）——卡片必须恒显「模拟」，同 sim.adas 惯例。"""
    a, _ = _agent(monkeypatch)
    r = asyncio.run(a.handle(_Intent("那是什么"), None, {"vision_frame_id": "vf_x"}))
    assert r.ui_card["simulated"] is True
    assert r.data["_prov"]["source"] == "simulated_camera"


def test_question_falls_back_to_raw_text(monkeypatch):
    a, calls = _agent(monkeypatch)
    asyncio.run(a.handle(_Intent("前面那个建筑是什么"), None, {"vision_frame_id": "vf_x"}))
    assert "前面那个建筑是什么" in str(calls[0]["messages"])


def test_manifest_declares_camera_permission():
    """看摄像头属敏感能力，权限必须在 manifest 声明（编排层据此过滤）。"""
    a = VisionAgent()
    perms = list(a.manifest.requires_permissions)
    assert "camera.frame" in perms
    # **不得声明 camera.read**：那是连续流权限，conventions §3 维持 ❌ 禁；
    # 单帧问答只需要单帧的权限，声明过宽就是权限膨胀。
    assert "camera.read" not in perms


def test_source_has_no_image_handling():
    """源码级：Agent 不得出现任何图像字节处理——它的职责只有转引用。"""
    import agents.vision.src.agent as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for lit in ("b64encode", "b64decode", "open(", "Image", "cv2"):
        assert lit not in src, f"vision agent 出现 {lit!r}——图像处理不该在这一层"
