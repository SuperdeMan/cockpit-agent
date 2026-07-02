"""R3.1 层 2：CloudClient 的 Hello 握手帧携带 channel session_token（云网关按 AUTH_REQUIRED 校验）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cloud_client import CloudClient


def test_hello_frame_carries_channel_token(monkeypatch):
    monkeypatch.setenv("CLOUD_CHANNEL_TOKEN", "demo-channel-v1")
    monkeypatch.setenv("VEHICLE_ID", "v7")
    frame = CloudClient()._hello_frame()
    assert frame.WhichOneof("body") == "hello"
    assert frame.hello.vehicle_id == "v7"
    assert frame.hello.session_token == "demo-channel-v1"
    assert frame.correlation_id == "v7-hello"


def test_hello_frame_empty_token_by_default(monkeypatch):
    monkeypatch.delenv("CLOUD_CHANNEL_TOKEN", raising=False)
    monkeypatch.setenv("VEHICLE_ID", "v1")
    frame = CloudClient()._hello_frame()
    # 默认空 token → 云侧默认放行（保持现状）。
    assert frame.hello.session_token == ""
