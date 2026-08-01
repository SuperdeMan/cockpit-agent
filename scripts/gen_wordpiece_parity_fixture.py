# -*- coding: utf-8 -*-
"""生成分词 parity 的**冻结 golden**（M5 P3 收尾，补 2026-07-30 评审的 INFO 项）。

## 为什么需要它

`test_edge_nlu.py::test_tokenizer_parity_against_transformers` 是守「训练/推理分词同源」
的硬测试——差一个 token，模型看到的就是另一句话，而它**不报错只变差**。但它同时要
① 底座 `models/nlu/base/vocab.txt`（gitignore）与 ② `transformers`（CI 不装 ML 栈），
于是在 CI 上**整体 skip**：唯一站岗的地方是有模型的开发机。

修法不是把 ML 栈搬进 CI，是把**参考答案**冻下来：本脚本用真 `BertTokenizerFast` 算出
参考 ids，连同**行为等价的词表子集**一起落盘；CI 上的测试只需 json，零依赖。

## 词表子集为什么是「行为等价」而不是「够用就行」

WordPiece 是贪心最长匹配——词表里多一个更长的子词，切法就变了。所以子集不能随手挑：
本脚本收录**全量词表中、其字面（去掉 `##`）是任一 basic token 子串**的全部条目，
于是贪心在子集上看到的候选与在全量上**逐个相同**。落盘前再逐条断言
`WordPiece(子集) == WordPiece(全量) == transformers`——**先证明它等价，再拿它当尺子**
（同 P2 收口那条：先验证门禁抓得住它本来该抓的缺陷，再启用它）。

用法（需要底座词表 + transformers，即开发机）：
    python scripts/gen_wordpiece_parity_fixture.py
产物：orchestrator/edge/tests/fixtures/wordpiece_parity.json
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "edge"))

import nlu as nlu_mod  # noqa: E402

VOCAB_TXT = ROOT / "models" / "nlu" / "base" / "vocab.txt"
CORPUS = ROOT / "test" / "eval_corpus" / "feishu_intents_full.jsonl"
OUT = ROOT / "orchestrator" / "edge" / "tests" / "fixtures" / "wordpiece_parity.json"
MAX_LEN = 32
N_CORPUS = 240
SEED = 20260801

# 刻意刁难的边界形态：英文/数字/小数/全角/emoji/纯标点/超长词/多空格/大小写/混排
ADVERSARIAL = [
    "FM87.5兆赫兹", "打开ACC", "温度调到26.5度", "Hello 世界", "！！！",
    "ＡＢＣ全角", "😀开空调", "a" * 120, "  空格  很多  ", "OK Google",
    "CO2浓度", "把AQI查一下", "第3排座椅", "ｱｲｳ半角假名", "café 拿铁",
    "①②③", "\t制表符\n换行", "1+1=2", "——破折号——", "«引号»",
]


def main() -> int:
    if not VOCAB_TXT.is_file():
        print(f"缺底座词表 {VOCAB_TXT}（先跑 scripts/fetch-edge-nlu-base.*）")
        return 1
    try:
        import transformers
    except ImportError:
        print("需要 transformers 才能生成参考答案（只在开发机跑，CI 不需要）")
        return 1

    ref = transformers.BertTokenizerFast.from_pretrained(str(VOCAB_TXT.parent))
    full_vocab = ref.get_vocab()

    rows = [json.loads(l) for l in CORPUS.open(encoding="utf-8") if l.strip()]
    random.seed(SEED)
    texts = [r["text"] for r in random.sample(rows, N_CORPUS) if (r.get("text") or "").strip()]
    texts += ADVERSARIAL

    # ── 行为等价的词表子集 ──
    basic_tokens = set()
    for t in texts:
        basic_tokens.update(nlu_mod.WordPiece._basic(t))
    keep = {}
    for token, tid in full_vocab.items():
        surface = token[2:] if token.startswith("##") else token
        if not surface:
            continue
        if any(surface in bt for bt in basic_tokens):
            keep[token] = tid
    for special in ("[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"):
        if special in full_vocab:
            keep[special] = full_vocab[special]

    mine_full = nlu_mod.WordPiece(full_vocab)
    mine_sub = nlu_mod.WordPiece(keep)
    cases = []
    for t in texts:
        want = list(ref(t, padding="max_length", truncation=True,
                        max_length=MAX_LEN)["input_ids"])
        got_full, _ = mine_full.encode(t, MAX_LEN)
        got_sub, _ = mine_sub.encode(t, MAX_LEN)
        assert got_full == want, f"本实现与 transformers 不一致，先修实现再生成：{t!r}"
        assert got_sub == want, (
            f"词表子集不等价（贪心看到的候选变了）：{t!r}\n sub={got_sub}\n ref={want}")
        cases.append({"text": t, "ids": want})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "_note": ("冻结的分词 golden。由 scripts/gen_wordpiece_parity_fixture.py 生成，"
                  "参考答案来自 transformers.BertTokenizerFast + 底座 vocab.txt；"
                  "vocab 是**行为等价的子集**（生成时逐条断言过与全量词表同结果）。"
                  "换底座 → 重新生成并人审 diff。"),
        "max_len": MAX_LEN,
        "seed": SEED,
        "vocab": keep,
        "cases": cases,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写出 {OUT}：{len(cases)} 条用例 / 词表子集 {len(keep)} 条（全量 {len(full_vocab)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
