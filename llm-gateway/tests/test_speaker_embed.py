"""网关声纹面单测（M4 P4）：三档决议 / 时长闸 / mock 确定性 / **不持有模板的源码铁律**。

网关这一侧只做「音频→向量」。最要紧的两条：
1. **模型缺失是常态之一不是异常**——28MB 模型在本机实测要十几分钟才拉得下来，
   `disabled` 档必须让整链逐字回落到 P4 之前，而不是把语音链路一起拖死。
2. **它不得持有任何模板**——模板一旦下发到这个无状态服务就删不干净，
   而 GDPR 硬删是本仓库已经立过的红线。
"""
from __future__ import annotations

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import speaker_embed as V  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """工厂有模块级缓存，逐例重置；并清掉可能污染判定的 env。"""
    V.reset_for_test()
    for k in ("VOICEPRINT_PROVIDER", "VOICEPRINT_MODEL_PATH", "VOICEPRINT_MIN_SPEECH_MS"):
        monkeypatch.delenv(k, raising=False)
    yield
    V.reset_for_test()


def _pcm(ms: int, seed: int = 1) -> bytes:
    n = int(16000 * ms / 1000)
    return struct.pack(f"<{n}h", *[(seed * i * 37) % 3000 - 1500 for i in range(n)])


# ── 时长 ──────────────────────────────────────────────────────────────────

def test_pcm_duration_ms_matches_16k_mono_s16le():
    assert V.pcm_duration_ms(_pcm(1500)) == 1500


def test_default_min_speech_is_1500ms():
    """1 秒以下的「嗯」「好」提不出稳定声纹——硬提只会得到一个随机方向的向量，
    那比认不出更糟（它会随机撞上某个模板）。"""
    assert V.min_speech_ms() == 1500


def test_min_speech_is_configurable(monkeypatch):
    monkeypatch.setenv("VOICEPRINT_MIN_SPEECH_MS", "2000")
    assert V.min_speech_ms() == 2000


# ── 三档决议 ──────────────────────────────────────────────────────────────

def test_explicit_off_disables_the_face(monkeypatch):
    monkeypatch.setenv("VOICEPRINT_PROVIDER", "off")
    assert V.resolve_provider() is None


def test_mock_provider_selected_explicitly(monkeypatch):
    monkeypatch.setenv("VOICEPRINT_PROVIDER", "mock")
    p = V.resolve_provider()
    assert p is not None and p.name == "mock"


def test_missing_model_disables_instead_of_crashing(monkeypatch):
    """**不是 fail-fast**：模型缺失属「未配置真实源」，与 ASR/TTS 的 off 档同口径。
    启动即炸会让「模型还没拉下来」变成「整个网关起不来」。"""
    monkeypatch.setenv("VOICEPRINT_MODEL_PATH", "/nonexistent/model.onnx")
    assert V.resolve_provider() is None
    assert "not found" in V.disabled_reason()


def test_resolution_is_cached(monkeypatch):
    monkeypatch.setenv("VOICEPRINT_PROVIDER", "mock")
    assert V.resolve_provider() is V.resolve_provider()


def test_decision_log_line_matches_conventions(monkeypatch, capsys):
    """决议日志格式对齐 conventions §9.4（`docker compose logs | grep "provider\\["`）。"""
    monkeypatch.setenv("VOICEPRINT_PROVIDER", "mock")
    V.resolve_provider()
    assert "provider[voiceprint]=mock" in capsys.readouterr().out


# ── mock 档 ───────────────────────────────────────────────────────────────

def test_mock_is_deterministic():
    m = V.MockVoiceprintProvider()
    assert m.embed(_pcm(1500, 7)) == m.embed(_pcm(1500, 7))


def test_mock_separates_different_audio():
    m = V.MockVoiceprintProvider()
    a, b = m.embed(_pcm(1500, 7)), m.embed(_pcm(1500, 9))
    assert sum(x * y for x, y in zip(a, b)) < 0.9   # 不同音频 → 不同方向


def test_mock_returns_unit_vector():
    v = V.MockVoiceprintProvider().embed(_pcm(1500))
    assert abs(sum(x * x for x in v) - 1.0) < 1e-6


def test_mock_empty_audio_returns_empty():
    assert V.MockVoiceprintProvider().embed(b"") == []


# ── 源码铁律 ──────────────────────────────────────────────────────────────

def _src(name: str) -> str:
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           name), encoding="utf-8") as f:
        return f.read()


def test_gateway_never_stores_templates():
    """网关侧不得出现任何模板存取——存储与比对全在 memory 服务（RFC §2.1）。
    出现这些词就意味着有人把模板下发到了无状态服务，GDPR 就删不干净了。"""
    src = _src("speaker_embed.py")
    for lit in ("INSERT", "SELECT", "asyncpg", "POSTGRES", "template", "_templates"):
        assert lit not in src, f"llm-gateway/speaker_embed.py 出现 {lit!r}——网关不得持有模板"


def test_gateway_voiceprint_never_touches_auth():
    """声纹不是鉴权因子（RFC §6.1 红线）——网关侧同样不得沾权限。"""
    src = _src("speaker_embed.py")
    for lit in ("granted_scopes", "permission", "require_confirm", "auth"):
        assert lit not in src


def test_identify_endpoint_falls_back_to_primary_on_any_failure():
    """任何失败路径都必须回 primary——声纹是可选增强，不能把一轮对话拖死。

    源码级断言：identify 段里每个**字面量** json_response 都要带 occupant_id；
    唯一返回变量的那个分支（成功路径）由下一条测试单独钉它的构造。
    """
    src = _src("http_server.py")
    start = src.index('@routes.post("/api/voiceprint/identify")')
    end = src.index('@routes.post("/api/voiceprint/enroll")')
    block = src[start:end]
    literal_returns = [c for c in block.split("web.json_response(")[1:] if c.startswith("{")]
    assert len(literal_returns) >= 4, "失败分支少于预期（disabled/too_short/no_user/error）"
    for chunk in literal_returns:
        assert '"occupant_id": "primary"' in chunk[:300], (
            "identify 有失败分支没有诚实降级到 primary")


def test_identify_success_payload_carries_the_decision():
    """成功路径返回的 out 必须同时带 occupant_id 与 decision——
    调用方（和 obs）要能区分「认出了」和「降级了」，不能只拿到一个说不清的 primary。"""
    src = _src("http_server.py")
    start = src.index('@routes.post("/api/voiceprint/identify")')
    end = src.index('@routes.post("/api/voiceprint/enroll")')
    block = src[start:end]
    assert '"occupant_id": resp.occupant_id' in block
    assert '"decision": resp.decision' in block


def test_audio_is_never_persisted_or_logged():
    """原始音频用完即弃：既不落盘也不进 obs（只报时长与判定）。"""
    src = _src("http_server.py")
    start = src.index('@routes.post("/api/voiceprint/identify")')
    end = src.index('@routes.delete("/api/voiceprint/{occupant_id}")')
    block = src[start:end]
    for lit in ("open(", "write(", "b64encode(pcm", "base64.b64encode"):
        assert lit not in block, f"声纹端点出现 {lit!r}——原始音频不得落盘/外传"
