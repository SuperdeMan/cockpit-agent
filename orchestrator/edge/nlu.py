"""端侧语义 NLU 推理（M5 P3）。训练 `scripts/train_edge_nlu.py`；RFC §5-P3。

## 姿态：与声纹 CAM++ 逐条对齐

`models/nlu/`（`edge_nlu.onnx` + `labels.json` + `vocab.json`）缺任何一件 → **决议 disabled，
整链回落 1727 行规则**。这不是「优雅降级」的客套话，是本仓已经验证过的形态：声纹模型缺失时
网关决议 disabled、其余功能照跑。端侧 NLU 同理——它是**增量供给**，不是新的单点。

## 为什么不用 transformers 的 tokenizer

端侧镜像只需要 `onnxruntime` + `numpy`；把 `transformers`（连带 tokenizers 的 Rust 扩展）
拉进车机镜像，为的只是一个 WordPiece——不值。这里是纯 Python 实现，**并由契约测试逐条比对
`BertTokenizerFast` 在全量语料上的输出**（`test_edge_nlu.py::test_tokenizer_parity`）。
自己写分词器最大的风险不是慢，是**和训练时的分词悄悄不一致**——那会让模型在线上系统性变差
而看不出原因（同「注册与识别不同信道」那一课：要比对的两端必须同源）。所以parity 是硬测试。

## θ_high / θ_low

架构 §3.2 的双阈值伪码写了半年没有真概率可接——规则命中是 0/1，置信度是硬编码字面量
（0.9/0.92/0.95）。这里第一次给出真 softmax 概率，复用既有 env
`FAST_INTENT_THRESHOLD_HIGH` / `FAST_INTENT_THRESHOLD_LOW`（后者此前**声明了但零消费方**）。

**默认 `EDGE_NLU_MODE=shadow`**：只算不用，把判定与规则的分歧打进 obs。放量按域，
且执行侧永远过 `LOCAL_INTENTS ∩ VAL ∩ require_confirm` 三闸——**识别错≠执行错**
（与「声纹不作鉴权因子」同一论证结构）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import unicodedata
from pathlib import Path

logger = logging.getLogger("edge.nlu")

_DIR = Path(os.getenv("EDGE_NLU_DIR", "")) if os.getenv("EDGE_NLU_DIR") else \
    Path(__file__).resolve().parents[2] / "models" / "nlu"


def mode() -> str:
    """`off` | `shadow`（默认，只算不用）。每次读——放量不需要重启。

    ⚠ **刻意还没有 `on`**。要真正在端侧执行，光有 (domain, object) 不够，还缺一件：
    **operate 抽取**（开/关/调到 N/加/减）——它属于准入判据②的确定形态、该走规则，
    但还没接上。（另一件「分类体系桥接」已由 `knowledge/nlu_objects.yaml` 补上，
    见 `to_val_objects`；但那张表只解决**识别侧对得上号**，不等于能执行。）
    在齐备之前**不留 `on` 这个值**——留了就是「一个没人测过却随时可能被打开的分支」
    （P2 对端侧初判进 prompt 的同款裁决）。放量路径见 RFC §5-P3 的后续。
    """
    m = os.getenv("EDGE_NLU_MODE", "shadow").strip().lower()
    return m if m in ("off", "shadow") else "shadow"


# ── 对象桥接（语料中文标签 → VAL 可执行 object）─────────────────────────────────

_BRIDGE_PATH = Path(__file__).resolve().parent / "knowledge" / "nlu_objects.yaml"
_bridge: dict[str, list[str]] | None = None
_bridge_lock = threading.Lock()


def _load_bridge() -> dict[str, list[str]]:
    global _bridge
    if _bridge is not None:
        return _bridge
    with _bridge_lock:
        if _bridge is None:
            try:
                import yaml
                raw = yaml.safe_load(_BRIDGE_PATH.read_text(encoding="utf-8")) or {}
                _bridge = {k: list(v or [])
                           for k, v in (raw.get("objects") or {}).items()}
            except Exception as e:       # 表缺失/损坏 → 全部 unmapped，影子少说一句而已
                logger.warning("对象桥接表不可用（影子按 unmapped 处理）：%s", e)
                _bridge = {}
    return _bridge


def equivalent_objects(label: str) -> list[str] | None:
    """语料中文对象标签 → **指同一件事的对象名集合**（VAL 的 + 规则侧的）。

    返回 `None` = 表里没有这个标签（新语料引入了没人裁过的对象，门禁会红）；
    返回 `[]` = 表里明确记着「连规则侧都没有对应名字」。两者影子都报 `unmapped`，
    但含义不同，别合并——前者是待办，后者是结论。
    """
    return _load_bridge().get((label or "").strip())


def val_objects(label: str, known: set[str]) -> list[str]:
    """可执行子集 = 等价名集合 ∩ VAL 的 objects（P3b 执行桥接用）。

    **派生而不是另写一列**：两列会各自漂移，而这种漂移不报错只变差
    （同「要比对的两端必须同源」）。`known` 由调用方从 VAL 知识库取，避免本模块反向依赖 VAL。
    """
    return [o for o in (equivalent_objects(label) or []) if o in known]


def theta_high() -> float:
    return _clamp(os.getenv("FAST_INTENT_THRESHOLD_HIGH", "0.85"), 0.85)


def theta_low() -> float:
    return _clamp(os.getenv("FAST_INTENT_THRESHOLD_LOW", "0.5"), 0.5)


def _clamp(raw: str, default: float) -> float:
    """越界/垃圾值不崩但也不许静默改变行为（skills min_score 钳 0 = 全量放行那一课）。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    if not (v == v) or v in (float("inf"), float("-inf")):
        return default
    return min(1.0, max(0.01, v))


# ── 纯 Python WordPiece（BERT 中文口径，do_lower_case=True）────────────────────

_CJK = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x20000, 0x2A6DF), (0x2A700, 0x2B73F),
        (0x2B740, 0x2B81F), (0x2B820, 0x2CEAF), (0xF900, 0xFAFF), (0x2F800, 0x2FA1F))


def _is_cjk(cp: int) -> bool:
    return any(a <= cp <= b for a, b in _CJK)


def _is_punct(ch: str) -> bool:
    cp = ord(ch)
    if 33 <= cp <= 47 or 58 <= cp <= 64 or 91 <= cp <= 96 or 123 <= cp <= 126:
        return True
    return unicodedata.category(ch).startswith("P")


def _is_control(ch: str) -> bool:
    if ch in ("\t", "\n", "\r"):
        return False
    return unicodedata.category(ch).startswith("C")


class WordPiece:
    """BertTokenizer(do_lower_case=True) 的等价实现。契约测试对全量语料逐条比对。

    **词表从 `vocab.json`（token→id）读，不从 `vocab.txt` 按行号推**。这是被 parity 测试
    当场抓出来的：底座的 vocab.txt 里有 3 个空行，transformers 的加载结果与「行号即 id」
    整体错开 1（`打` 在文件第 2803 行、在 tokenizer 里是 2802），于是每个 token 都喂错。
    结论不是「补一条跳空行的规则」——那是猜别人的加载器；而是**导出时把 tokenizer 自己的
    映射落成文件**（`scripts/train_edge_nlu.py::export_onnx`）。我只重实现**算法**
    （basic + wordpiece 贪心最长匹配），不重实现**词表加载**。
    """

    def __init__(self, vocab: dict[str, int]):
        self.vocab = vocab
        self.unk = self.vocab.get("[UNK]", 100)
        self.cls = self.vocab.get("[CLS]", 101)
        self.sep = self.vocab.get("[SEP]", 102)
        self.pad = self.vocab.get("[PAD]", 0)

    @classmethod
    def from_json(cls, path: Path) -> "WordPiece":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def _basic(text: str) -> list[str]:
        out = []
        for ch in text:
            if _is_control(ch) or ord(ch) == 0xFFFD:
                continue
            if _is_cjk(ord(ch)):
                out.append(f" {ch} ")
            elif ch.isspace():
                out.append(" ")
            else:
                out.append(ch)
        toks = []
        for tok in "".join(out).split():
            tok = tok.lower()
            tok = "".join(c for c in unicodedata.normalize("NFD", tok)
                          if unicodedata.category(c) != "Mn")
            buf, cur = [], ""
            for ch in tok:
                if _is_punct(ch):
                    if cur:
                        buf.append(cur)
                        cur = ""
                    buf.append(ch)
                else:
                    cur += ch
            if cur:
                buf.append(cur)
            toks.extend(buf)
        return toks

    def _wordpiece(self, token: str) -> list[int]:
        if len(token) > 100:
            return [self.unk]
        out, start = [], 0
        while start < len(token):
            end = len(token)
            cur = None
            while start < end:
                sub = token[start:end]
                if start > 0:
                    sub = "##" + sub
                if sub in self.vocab:
                    cur = self.vocab[sub]
                    break
                end -= 1
            if cur is None:
                return [self.unk]
            out.append(cur)
            start = end
        return out

    def encode(self, text: str, max_len: int) -> tuple[list[int], list[int]]:
        ids = [self.cls]
        for tok in self._basic(text):
            ids.extend(self._wordpiece(tok))
            if len(ids) >= max_len - 1:
                break
        ids = ids[:max_len - 1] + [self.sep]
        mask = [1] * len(ids) + [0] * (max_len - len(ids))
        ids = ids + [self.pad] * (max_len - len(ids))
        return ids, mask


# ── 推理 ────────────────────────────────────────────────────────────────────

class EdgeNLU:
    """懒加载 + 单例。任何缺失/异常 → `available=False`，调用方按 None 处理。"""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else _DIR
        self._sess = None
        self._tok: WordPiece | None = None
        self._labels: dict = {}
        self._tried = False
        self._lock = threading.Lock()
        self.reason = ""

    @property
    def available(self) -> bool:
        self._ensure()
        return self._sess is not None

    def _ensure(self) -> None:
        if self._tried:
            return
        with self._lock:
            if self._tried:
                return
            self._tried = True
            onnx, labels, vocab = (self.root / "edge_nlu.onnx",
                                   self.root / "labels.json", self.root / "vocab.json")
            missing = [p.name for p in (onnx, labels, vocab) if not p.is_file()]
            if missing:
                self.reason = f"缺文件 {missing}"
                logger.info("edge NLU disabled：%s（整链回落规则）", self.reason)
                return
            try:
                import onnxruntime as ort
                self._labels = json.loads(labels.read_text(encoding="utf-8"))
                self._tok = WordPiece.from_json(vocab)
                so = ort.SessionOptions()
                so.intra_op_num_threads = 1      # 端侧：单条短句，多线程只增调度开销
                so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._sess = ort.InferenceSession(str(onnx), so,
                                                  providers=["CPUExecutionProvider"])
                logger.info("edge NLU ready：%d 域 / %d 对象",
                            len(self._labels.get("domains") or []),
                            len(self._labels.get("objects") or []))
            except Exception as e:          # 装不上 onnxruntime、模型损坏、opset 不支持…
                self._sess = None
                self.reason = f"{type(e).__name__}: {e}"
                logger.warning("edge NLU disabled：%s（整链回落规则）", self.reason)

    def classify(self, text: str) -> dict | None:
        """→ {domain, object, conf, conf_domain} 或 None（不可用/空输入）。"""
        text = (text or "").strip()
        if not text or not self.available:
            return None
        try:
            max_len = int(self._labels.get("max_len") or 32)
            ids, mask = self._tok.encode(text, max_len)
            out = self._sess.run(None, {"input_ids": [ids], "attention_mask": [mask]})
            dom = _softmax(out[0][0])
            obj = _softmax(out[1][0])
            di = max(range(len(dom)), key=dom.__getitem__)
            oi = max(range(len(obj)), key=obj.__getitem__)
            return {"domain": self._labels["domains"][di],
                    "object": self._labels["objects"][oi],
                    "conf": round(float(obj[oi]), 4),
                    "conf_domain": round(float(dom[di]), 4)}
        except Exception as e:
            logger.warning("edge NLU 推理失败（本轮按 None 处理）: %s", e)
            return None


def _softmax(xs) -> list[float]:
    m = max(xs)
    exps = [pow(2.718281828459045, float(x) - float(m)) for x in xs]
    s = sum(exps) or 1.0
    return [e / s for e in exps]


_default: EdgeNLU | None = None


def default() -> EdgeNLU:
    global _default
    if _default is None:
        _default = EdgeNLU()
    return _default
