"""声纹提取面（M4 P4）：PCM → 说话人 embedding。**只做模型推理，不持有任何模板。**

**为什么叫 speaker_embed 而不是 voiceprint**：`memory/voiceprint.py` 是模板与判定层，
两边同名会在跑全量单测时互相劫持 `sys.modules`（本仓库有前科：providers 通用包名劫持）。
名字也顺带把分工说清楚了——这边只出向量，「是谁」是记忆域的事。

分工（RFC `docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md` §2.1）：
本模块把音频变成向量，向量交给 memory 服务比对。**模板绝不下发到这里**——声纹是生物特征，
一旦扩散到无状态服务就删不干净，而 GDPR 硬删是本仓库已经立过的红线（M2 关系边）。

**原始音频用完即弃**：提完 embedding 就出作用域，不落盘、不进 obs、不跨服务。

三档决议（对齐 conventions §9.4）：
  campplus  模型文件在且 sherpa-onnx 可导入 → 真实推理
  mock      显式 VOICEPRINT_PROVIDER=mock → 确定性伪向量，供离线单测/CI
  disabled  模型缺失/依赖缺失 → **整个声纹面诚实禁用**，occupant_id 恒 primary
            = 逐字回落到 P4 之前。模型 28MB 且本机下载不稳（R3.6 前科），
            这一档不是兜底而是常态之一，不能让它阻塞任何别的功能。
"""
from __future__ import annotations
import hashlib
import logging
import math
import os
import struct

logger = logging.getLogger("llm.voiceprint")

# 识别所需的最短有效语音。低于此长度直接判 too_short——1 秒以下的「嗯」「好」提不出
# 稳定声纹，硬提只会得到一个随机方向的向量，那比认不出更糟（它会随机撞上某个模板）。
DEFAULT_MIN_SPEECH_MS = 1500
_DEFAULT_MODEL_PATH = "/app/models/voiceprint/campplus_zh-cn_16k-common.onnx"
_SAMPLE_RATE = 16000


def min_speech_ms() -> int:
    return int(os.getenv("VOICEPRINT_MIN_SPEECH_MS", str(DEFAULT_MIN_SPEECH_MS)))


def pcm_duration_ms(pcm: bytes) -> int:
    """16k mono s16le 的时长。"""
    return int(len(pcm) / 2 / _SAMPLE_RATE * 1000)


class BaseVoiceprintProvider:
    """换厂商只加子类（同 BaseS2SProvider 的形态）。"""
    name = "base"
    model = ""
    dim = 0

    def embed(self, pcm: bytes) -> list[float]:
        raise NotImplementedError


class CampPlusProvider(BaseVoiceprintProvider):
    """3D-Speaker CAM++（192 维）经 sherpa-onnx 推理。

    **为什么用 sherpa-onnx 而不是裸 onnxruntime**：CAM++ 的输入不是 PCM 而是 Kaldi 口径的
    80 维 fbank（povey 窗 / 25ms-10ms / 均值归一），自己用 numpy 复刻一份「差不多的 fbank」
    是本期最容易埋雷的地方——特征稍有偏差，模型照样输出一个 192 维向量，同人余弦却会掉到
    和异人一个水平，而且从代码上完全看不出错。sherpa-onnx 内部就是这个模型配套的特征实现，
    且仓库已经在用它的模型族（R4.3 KWS/VAD），依赖是同一家。
    """
    name = "campplus"
    dim = 192

    def __init__(self, model_path: str):
        import sherpa_onnx  # noqa: PLC0415 —— 缺依赖时由工厂捕获并决议 disabled
        self.model = os.path.basename(model_path)
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=model_path,
                num_threads=int(os.getenv("VOICEPRINT_THREADS", "1")),
                provider="cpu"))
        self.dim = int(self._extractor.dim)

    def embed(self, pcm: bytes) -> list[float]:
        n = len(pcm) // 2
        if n <= 0:
            return []
        samples = [x / 32768.0 for x in struct.unpack(f"<{n}h", pcm[:n * 2])]
        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate=_SAMPLE_RATE, waveform=samples)
        stream.input_finished()
        return list(self._extractor.compute(stream))


class MockVoiceprintProvider(BaseVoiceprintProvider):
    """确定性伪向量：同一段音频恒得同一向量，不同音频得不同向量。

    **它不是「假装能识别」**——它让协议、注册、隔离、级联删这些逻辑在没有模型的机器上也能
    被测到。真实性由 `REQUIRE_REAL_PROVIDERS=on` 时 fail-fast 保证（同 ASR/TTS mock 档的
    既有口径）。伪向量对「同一个人的不同句子」不具备相似性，故 e2e 用同一段音频代表同一人。
    """
    name = "mock"
    model = "mock-voiceprint-v1"
    dim = 64

    def embed(self, pcm: bytes) -> list[float]:
        if not pcm:
            return []
        h = hashlib.sha256(pcm).digest()
        # 从摘要展开成 dim 维；再归一由上层做（memory 侧统一归一）。
        vals = []
        seed = h
        while len(vals) < self.dim:
            seed = hashlib.sha256(seed).digest()
            vals.extend(b / 255.0 - 0.5 for b in seed)
        v = vals[:self.dim]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


_provider: BaseVoiceprintProvider | None = None
_resolved = False
_disabled_reason = ""


def resolve_provider() -> BaseVoiceprintProvider | None:
    """三档决议 + 决议日志（conventions §9.4）。返回 None = 该面禁用。"""
    global _provider, _resolved, _disabled_reason
    if _resolved:
        return _provider
    _resolved = True
    want = os.getenv("VOICEPRINT_PROVIDER", "auto").strip().lower()

    if want in ("off", "disabled"):
        _disabled_reason = "disabled by VOICEPRINT_PROVIDER"
        print("provider[voiceprint]=disabled", flush=True)
        return None
    if want == "mock":
        # 严格栈（REQUIRE_REAL_PROVIDERS=on）下 mock 直接拒绝启动——与 llm/embed/asr/tts
        # 四闸同一契约。disabled 档不在此列：它是「未配置真实源」，同 ASR/TTS 的 off 档。
        from providers import _strict_mock_gate
        _strict_mock_gate("voiceprint", "VOICEPRINT_PROVIDER=mock")
        _provider = MockVoiceprintProvider()
        print("provider[voiceprint]=mock", flush=True)
        return _provider

    path = os.getenv("VOICEPRINT_MODEL_PATH", _DEFAULT_MODEL_PATH)
    if not os.path.exists(path):
        # **不是 fail-fast**：模型缺失属「未配置真实源」，与 ASR/TTS 的 off 档同口径。
        # 显式要求真实源的场合由 REQUIRE_REAL_PROVIDERS 那一闸管（见 health.py）。
        _disabled_reason = f"model not found: {path}"
        print("provider[voiceprint]=disabled", flush=True)
        logger.warning("voiceprint disabled: %s（跑 scripts/fetch-voice-models.* 拉模型）",
                       _disabled_reason)
        return None
    try:
        _provider = CampPlusProvider(path)
    except Exception as e:
        _disabled_reason = f"init failed: {e}"
        print("provider[voiceprint]=disabled", flush=True)
        logger.warning("voiceprint disabled: %s", _disabled_reason)
        return None
    print(f"provider[voiceprint]=campplus(real) model={_provider.model} "
          f"dim={_provider.dim}", flush=True)
    return _provider


def disabled_reason() -> str:
    return _disabled_reason


def reset_for_test() -> None:
    """单测隔离用（工厂有模块级缓存）。"""
    global _provider, _resolved, _disabled_reason
    _provider, _resolved, _disabled_reason = None, False, ""
