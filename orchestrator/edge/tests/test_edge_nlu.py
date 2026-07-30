"""端侧语义 NLU 契约测试（M5 P3）。实现 `orchestrator/edge/nlu.py`。

四件「错了会很贵」的性质：
  ① **分词 parity**：纯 Python WordPiece 必须与训练时用的 `BertTokenizerFast` 逐 token 相同。
     自己写分词器最大的风险不是慢，是**和训练时悄悄不一致**——模型会在线上系统性变差而
     毫无报错（同「注册与识别不同信道」那一课：要比对的两端必须同源采集）。
     transformers 装不上时 skip（CI 不装 ML 栈），但**装了就必须逐条过**。
  ② **缺模型即 disabled**：models/nlu/ 缺任何一件都不许崩、不许抛——整链回落 1727 行规则
     （声纹 CAM++ 缺失时网关决议 disabled 的同款姿态）。
  ③ **阈值钳制**：垃圾/越界 env 不崩，也不许静默变成「全量放行」（skills min_score 钳 0 那一课）。
  ④ **默认 shadow**：新识别通道的默认挡位必须是「只算不用」——放量是显式动作。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import nlu as nlu_mod  # noqa: E402

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_VOCAB = os.path.join(_ROOT, "models", "nlu", "base", "vocab.txt")
_CORPUS = os.path.join(_ROOT, "test", "eval_corpus", "feishu_intents_full.jsonl")


# ── ① 分词 parity ────────────────────────────────────────────────────────────

@pytest.mark.skipif(not os.path.isfile(_VOCAB),
                    reason="底座 vocab 未拉取（scripts/fetch-edge-nlu-base.*）")
def test_tokenizer_parity_against_transformers():
    """全量语料逐条比对 BertTokenizerFast。差一个 token，模型看到的就是另一句话。"""
    transformers = pytest.importorskip("transformers", reason="CI 不装 ML 栈")
    from pathlib import Path
    ref = transformers.BertTokenizerFast.from_pretrained(str(Path(_VOCAB).parent))
    # 词表用 tokenizer 自己的映射（=导出物 vocab.json 的内容），不按 vocab.txt 行号推——
    # 底座 vocab.txt 有 3 个空行，行号与真实 id 整体错开 1，本测试首跑正是这么抓到的。
    mine = nlu_mod.WordPiece(ref.get_vocab())
    texts = []
    with open(_CORPUS, encoding="utf-8") as f:
        for line in f:
            t = (json.loads(line).get("text") or "").strip()
            if t:
                texts.append(t)
    assert len(texts) > 8000, "语料没读全？"
    # 加几条刻意刁难的：英文/数字/标点/全角/emoji/长词
    texts += ["FM87.5兆赫兹", "打开ACC", "温度调到26.5度", "Hello 世界", "！！！",
              "ＡＢＣ全角", "😀开空调", "a" * 120, "  空格  很多  "]
    bad = []
    for t in texts:
        got, _ = mine.encode(t, 32)
        want = ref(t, padding="max_length", truncation=True, max_length=32)["input_ids"]
        if got != list(want):
            bad.append((t, got[:12], list(want)[:12]))
            if len(bad) >= 5:
                break
    assert not bad, "分词与 transformers 不一致：" + "; ".join(
        f"{t!r} mine={a} ref={b}" for t, a, b in bad)


@pytest.mark.skipif(not os.path.isfile(_VOCAB), reason="底座 vocab 未拉取")
def test_encode_shape_and_padding():
    from pathlib import Path
    transformers = pytest.importorskip("transformers", reason="CI 不装 ML 栈")
    mine = nlu_mod.WordPiece(
        transformers.BertTokenizerFast.from_pretrained(str(Path(_VOCAB).parent)).get_vocab())
    ids, mask = mine.encode("打开座椅加热", 32)
    assert len(ids) == len(mask) == 32
    assert ids[0] == mine.cls and mine.sep in ids
    assert sum(mask) == ids.index(mine.sep) + 1        # mask 只盖到 [SEP]
    long_ids, long_mask = mine.encode("打开" * 100, 32)
    assert len(long_ids) == 32 and long_ids[-1] == mine.sep, "超长必须截断并保留 [SEP]"
    assert sum(long_mask) == 32


# ── ② 缺模型即 disabled ─────────────────────────────────────────────────────

def test_missing_model_is_disabled_not_crash(tmp_path):
    e = nlu_mod.EdgeNLU(root=tmp_path)
    assert e.available is False
    assert "缺文件" in e.reason
    assert e.classify("打开空调") is None      # 调用方按 None 处理，不是异常


def test_partial_model_is_disabled(tmp_path):
    """只有 onnx 没有 labels/vocab.json 也必须 disabled——半个模型比没有模型更危险。"""
    (tmp_path / "edge_nlu.onnx").write_bytes(b"not really an onnx")
    e = nlu_mod.EdgeNLU(root=tmp_path)
    assert e.available is False


def test_corrupt_model_is_disabled(tmp_path):
    """三个文件**都在**但 onnx 是坏的 → 捕获异常后 disabled，绝不把异常抛给端侧主链。

    ⚠ 三个文件必须齐：只要少一个，本例就会因为「缺文件」而 disabled——**测试通过了，
    但通过的是另一条路**，坏 onnx 那条分支一次都没跑到。首版正是漏写了 vocab.json。"""
    (tmp_path / "edge_nlu.onnx").write_bytes(b"garbage")
    (tmp_path / "labels.json").write_text(
        json.dumps({"domains": ["setting"], "objects": ["seat"], "max_len": 32}),
        encoding="utf-8")
    (tmp_path / "vocab.json").write_text(
        json.dumps({"[PAD]": 0, "[UNK]": 1, "[CLS]": 2, "[SEP]": 3, "打": 4, "开": 5}),
        encoding="utf-8")
    pytest.importorskip("onnxruntime", reason="没装 onnxruntime 时 disabled 由缺包给出，非本例意图")
    e = nlu_mod.EdgeNLU(root=tmp_path)
    assert e.available is False
    assert "缺文件" not in e.reason, f"走的是缺文件那条路，坏 onnx 分支没跑到：{e.reason}"
    assert e.classify("打开空调") is None


# ── ③ 阈值钳制 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("0.7", 0.7), ("0", 0.01), ("-1", 0.01), ("2.5", 1.0), ("nan", 0.85),
    ("inf", 0.85), ("", 0.85), ("abc", 0.85),
])
def test_theta_high_clamped(monkeypatch, raw, expected):
    """钳 0 = 全量放行是**静默**的行为改变，必须挡在下限之外（skills min_score 同款）。"""
    monkeypatch.setenv("FAST_INTENT_THRESHOLD_HIGH", raw)
    assert nlu_mod.theta_high() == pytest.approx(expected)


def test_theta_low_reuses_existing_env(monkeypatch):
    """`FAST_INTENT_THRESHOLD_LOW` 此前**声明了却零消费方**（.env/compose/conventions 三处
    都有，代码只读 _HIGH）——P3 接的正是它，不新增一个平行开关。"""
    monkeypatch.setenv("FAST_INTENT_THRESHOLD_LOW", "0.42")
    assert nlu_mod.theta_low() == pytest.approx(0.42)
    monkeypatch.delenv("FAST_INTENT_THRESHOLD_LOW")
    assert nlu_mod.theta_low() == pytest.approx(0.5)


# ── ④ 默认挡位 ──────────────────────────────────────────────────────────────

def test_default_mode_is_shadow(monkeypatch):
    monkeypatch.delenv("EDGE_NLU_MODE", raising=False)
    assert nlu_mod.mode() == "shadow", "新识别通道的默认必须是「只算不用」"


@pytest.mark.parametrize("raw", ["", "OFF", " off ", "shadow", "垃圾值"])
def test_mode_never_raises(monkeypatch, raw):
    monkeypatch.setenv("EDGE_NLU_MODE", raw)
    assert nlu_mod.mode() in ("off", "shadow")


@pytest.mark.parametrize("raw", ["on", "ON", " on "])
def test_on_mode_does_not_exist_yet(monkeypatch, raw):
    """`on`（真在端侧执行）还缺分类体系桥接与 operate 抽取——**在齐备前不许存在这个值**，
    否则就是「一个没人测过却随时可能被打开的分支」（同 P2 对端侧初判进 prompt 的裁决）。"""
    monkeypatch.setenv("EDGE_NLU_MODE", raw)
    assert nlu_mod.mode() == "shadow"
