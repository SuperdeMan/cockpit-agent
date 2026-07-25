"""主动发布客户端契约 + 全仓迁移护栏（M3 P0）。

两件事：
1. `runtime/proactive.py` 的 **fail-open**——治理器缺席必须自动直发老主题，
   否则治理器一挂，用户显式约定的提醒就被静默吞掉。
2. **迁移不许漏**：全仓（除客户端与治理器自身）不得再出现直发 `agent.proactive` 的字面量。
"""
from __future__ import annotations

import os
import pathlib

import pytest

from runtime.proactive import (DIRECT, GOVERNED, GOVERNOR_SUBJECT, LEGACY_SUBJECT,
                               SKIPPED, publish_proactive)


class FakeNats:
    def __init__(self, *, responds=True, raise_on_publish=False):
        self.responds = responds
        self.raise_on_publish = raise_on_publish
        self.requests: list[tuple[str, bytes]] = []
        self.published: list[tuple[str, bytes]] = []

    async def request(self, subject, data, timeout=None):
        self.requests.append((subject, data))
        if not self.responds:
            raise TimeoutError("no responders")
        return type("Msg", (), {"data": b'{"accepted":true}'})()

    async def publish(self, subject, data):
        if self.raise_on_publish:
            raise RuntimeError("bus down")
        self.published.append((subject, data))


@pytest.mark.asyncio
async def test_governor_present_takes_ownership():
    nc = FakeNats()
    assert await publish_proactive(nc, {"speech": "x"}) == GOVERNED
    assert nc.requests[0][0] == GOVERNOR_SUBJECT
    assert nc.published == [], "治理器接手后不该同时直发（会双份播报）"


@pytest.mark.asyncio
async def test_governor_absent_falls_back_to_legacy_subject():
    nc = FakeNats(responds=False)
    assert await publish_proactive(nc, {"speech": "x"}) == DIRECT
    assert nc.published[0][0] == LEGACY_SUBJECT
    assert b'"x"' in nc.published[0][1] or "x" in nc.published[0][1].decode()


@pytest.mark.asyncio
async def test_no_nats_is_skipped_not_crash():
    assert await publish_proactive(None, {"speech": "x"}) == SKIPPED


@pytest.mark.asyncio
async def test_bus_down_is_skipped_not_raised():
    nc = FakeNats(responds=False, raise_on_publish=True)
    assert await publish_proactive(nc, {"speech": "x"}) == SKIPPED


def test_no_producer_publishes_to_legacy_subject_directly():
    """迁移漏一个 = 那一路绕过全部治理。用字面量断言把它钉死。"""
    root = pathlib.Path(__file__).resolve().parents[2]
    allowed = {
        root / "runtime" / "proactive.py",       # 客户端（fail-open 直发口）
        root / "proactive" / "main.py",          # 治理器（输出端）
    }
    scan_dirs = ["agents", "memory", "orchestrator", "registry", "llm-gateway",
                 "observability", "payment-gateway", "security", "runtime", "proactive"]
    offenders = []
    for d in scan_dirs:
        base = root / d
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if path in allowed or "tests" in path.parts or "__pycache__" in path.parts:
                continue
            src = path.read_text(encoding="utf-8", errors="ignore")
            if '"agent.proactive"' in src or "'agent.proactive'" in src:
                offenders.append(str(path.relative_to(root)))
    assert not offenders, (
        "以下文件仍直发 agent.proactive，会绕过主动治理器："
        + ", ".join(offenders) + "；改用 runtime.proactive.publish_proactive")


def test_governor_service_is_not_registered_as_agent():
    """治理器是基础设施服务，不是 Agent——不该有 manifest、不进注册中心路由。"""
    root = pathlib.Path(__file__).resolve().parents[2]
    assert not (root / "proactive" / "manifest.yaml").exists()
    assert not os.path.isdir(root / "agents" / "proactive")
