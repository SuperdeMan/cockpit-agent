"""端侧语义 NLU 训练（M5 P3）。RFC `docs/design/2026-07-28-intent-accuracy-data-flywheel.md` §5-P3。

把端侧意图识别从「1727 行手写规则 + 硬编码置信度字面量」换成**小型中文 encoder 分类头**，
产出**真概率**——架构 §3.2 的 θ_high/θ_low 双阈值伪码写了半年，第一次有真概率可接。

## 训什么

飞书语料 8590 条自带两个金标：`domain`（9 类）与 `object`（83 类）。两个分类头共享
encoder（`iic/nlp_structbert_backbone_tiny_std`：4 层 / hidden 256 / 8.9M 参数 / 35MB，
ModelScope 拉取——**不是 HF**：本机 HF LFS 实测 28-47KB/s，ModelScope 3MB/s）。

**operate（开/关/调到/加/减）刻意不进模型**：它是封闭动词类、规则本来就准，且属于准入判据②
（端侧延迟敏感的确定形态）。模型只解决「说的是哪个对象」——那才是规则真正认不出来的部分。

## 两个评测口径，第二个才是决策依据

- **A 随机分层 holdout**：同分布上界。语料里同一条指令有大量 paraphrase
  （「打开座椅加热」「请将右座椅加热打开」…），随机切分会把同族说法切到两侧，
  **这个数会被泄漏抬高**，只能当上界看。
- **B 规则命中 → 规则漏判 迁移**（headline）：只用**规则已经认得**的 6510 条训练，
  在**规则认不出**的 2080 条上测。它直接回答 P3 的那个问题——「拿规则已知的知识训一个模型，
  它能不能接住规则接不住的说法」。B 高于 A 是不可能的；**B 才是覆盖率能涨多少的依据**。

用法：
  python scripts/train_edge_nlu.py --lane transfer     # B（默认，决策依据）
  python scripts/train_edge_nlu.py --lane holdout      # A（同分布上界）
  python scripts/train_edge_nlu.py --lane both --export models/nlu
前置：`models/nlu/base/`（config.json + vocab.txt + pytorch_model.bin）由
`scripts/fetch-edge-nlu-base.sh|ps1` 拉取；缺失即退出（不静默降级——训练不是运行时）。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "orchestrator" / "edge"))

_CORPUS = _ROOT / "test" / "eval_corpus" / "feishu_intents_full.jsonl"
_BASE = _ROOT / "models" / "nlu" / "base"
_OUT = _ROOT / "models" / "nlu"
_EVAL = _ROOT / "docs" / "reviews" / "eval"
_REPORT_JSON = _EVAL / "edge_nlu_train.json"
_REPORT_MD = _EVAL / "edge_nlu_train.md"

MAX_LEN = 32          # 语料 P99 长度 20 字；32 足够且省一半算力
SEED = 20260730


def load_rows() -> list[dict]:
    from fast_intent import classify_structured
    rows = []
    for i, line in enumerate(_CORPUS.open(encoding="utf-8")):
        r = json.loads(line)
        text = str(r.get("text") or "").strip()
        dom = str(r.get("domain") or "").strip()
        obj = str(r.get("object") or "").strip()
        if not text or not dom or not obj:
            continue          # 域/对象金标缺失的（11 条空 domain）不参与——没有金标就没有监督
        rows.append({"idx": i, "text": text, "domain": dom, "object": obj,
                     "edge_owned": r.get("edge_expected") is not False,
                     "rule_hit": classify_structured(text) is not None})
    return rows


def split_transfer(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """B 车道：规则命中 → 规则漏判。

    ⚠ 一个必须处理的偏斜：有些 object 的**全部**行都是规则漏判（如「交互」「个人中心」
    100% miss）。这些类在训练集里一条都没有，测试时**必然全错**——那不是模型的问题，是
    「训练集里没有这个类」。它们照样计入总分（覆盖率就是要它们），但报告里单独列出来，
    否则会把「语料不够」误读成「模型不行」。
    """
    train = [r for r in rows if r["rule_hit"]]
    test = [r for r in rows if not r["rule_hit"]]
    return train, test


def split_holdout(rows: list[dict], frac: float = 0.15) -> tuple[list[dict], list[dict]]:
    rnd = random.Random(SEED)
    by = {}
    for r in rows:
        by.setdefault((r["domain"], r["object"]), []).append(r)
    train, test = [], []
    for _, group in sorted(by.items()):
        rnd.shuffle(group)
        k = max(1, int(len(group) * frac)) if len(group) > 2 else 0
        test.extend(group[:k])
        train.extend(group[k:])
    return train, test


def build_model(n_domain: int, n_object: int):
    import torch
    from torch import nn
    from transformers import BertConfig, BertModel

    class TwoHead(nn.Module):
        """共享 encoder + 两个线性头。**刻意不加中间层**：8590 条语料上多一层就多一分过拟合，
        而这两个任务的判别信号（对象词 + 动词）本来就在 [CLS] 里线性可分。"""

        def __init__(self):
            super().__init__()
            cfg = BertConfig.from_pretrained(str(_BASE))
            self.bert = BertModel.from_pretrained(str(_BASE), config=cfg)
            h = cfg.hidden_size
            self.drop = nn.Dropout(0.1)
            self.domain = nn.Linear(h, n_domain)
            self.object = nn.Linear(h, n_object)

        def forward(self, input_ids, attention_mask, token_type_ids=None):
            out = self.bert(input_ids=input_ids, attention_mask=attention_mask)
            cls = self.drop(out.last_hidden_state[:, 0])
            return self.domain(cls), self.object(cls)

    return TwoHead()


def encode(tok, texts: list[str]):
    import torch
    enc = tok(texts, padding="max_length", truncation=True, max_length=MAX_LEN,
              return_tensors="pt")
    return enc["input_ids"], enc["attention_mask"]


def _toolchain() -> dict[str, str]:
    """训练用到的 ML 栈版本进报告 meta。

    2026-08-04 立的账：本脚本此前不记版本，于是「同语料同种子同划分、读数差 9 点」
    这件事查了半天才归因到版本漂移上（findings §9.5）。项目在 skills eval 上早有同一条
    纪律——**跑批条件全进 meta，缺了跨 run 对比就是拿苹果比橘子**——训练侧漏了。
    """
    import importlib
    out: dict[str, str] = {}
    for name in ("torch", "transformers", "onnxruntime", "numpy"):
        try:
            out[name] = getattr(importlib.import_module(name), "__version__", "?")
        except Exception:                    # 缺包不该拦下报告生成
            out[name] = "absent"
    return out


def run_lane(lane: str, rows: list[dict], args) -> dict:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
    from transformers import BertTokenizerFast

    torch.manual_seed(SEED)
    random.seed(SEED)
    torch.set_num_threads(max(2, (os.cpu_count() or 4) - 2))

    train, test = (split_transfer(rows) if lane == "transfer"
                   else split_holdout(rows))
    doms = sorted({r["domain"] for r in rows})
    objs = sorted({r["object"] for r in rows})
    di = {d: i for i, d in enumerate(doms)}
    oi = {o: i for i, o in enumerate(objs)}
    # 训练集里一条样本都没有的类——测试必然全错，但那是语料问题不是模型问题
    seen_objs = {r["object"] for r in train}
    unseen = sorted(set(objs) - seen_objs)

    tok = BertTokenizerFast.from_pretrained(str(_BASE))
    model = build_model(len(doms), len(objs))

    def to_ds(part):
        ids, mask = encode(tok, [r["text"] for r in part])
        return TensorDataset(ids, mask,
                             torch.tensor([di[r["domain"]] for r in part]),
                             torch.tensor([oi[r["object"]] for r in part]))

    dl = DataLoader(to_ds(train), batch_size=args.batch_size, shuffle=True)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    steps = len(dl) * args.epochs
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps,
                                                pct_start=0.1)
    lossf = nn.CrossEntropyLoss()
    print(f"\n=== 车道 {lane}：train {len(train)} / test {len(test)}｜"
          f"domain {len(doms)} 类 / object {len(objs)} 类"
          f"（训练集未见 object {len(unseen)} 类）===")
    t0 = time.time()
    model.train()
    for ep in range(1, args.epochs + 1):
        tot = 0.0
        for ids, mask, yd, yo in dl:
            opt.zero_grad()
            ld, lo = model(ids, mask)
            loss = lossf(ld, yd) + lossf(lo, yo)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            tot += loss.item()
        print(f"  epoch {ep}/{args.epochs}  loss {tot/max(1,len(dl)):.4f}"
              f"  {time.time()-t0:.0f}s")

    model.eval()
    dom_ok = obj_ok = both_ok = 0
    conf_bins = Counter()
    per_obj = {}
    rows_out = []
    with torch.no_grad():
        for i in range(0, len(test), 128):
            part = test[i:i + 128]
            ids, mask = encode(tok, [r["text"] for r in part])
            ld, lo = model(ids, mask)
            pd_, po = torch.softmax(ld, -1), torch.softmax(lo, -1)
            cd, id_ = pd_.max(-1)
            co, io_ = po.max(-1)
            for j, r in enumerate(part):
                gd, go = r["domain"], r["object"]
                d_ok = doms[int(id_[j])] == gd
                o_ok = objs[int(io_[j])] == go
                dom_ok += d_ok
                obj_ok += o_ok
                both_ok += (d_ok and o_ok)
                c = float(co[j])
                conf_bins[f"{int(c*10)/10:.1f}"] += 1
                s = per_obj.setdefault(go, {"n": 0, "ok": 0,
                                            "unseen": go in unseen})
                s["n"] += 1
                s["ok"] += o_ok
                rows_out.append({"text": r["text"], "gold_object": go,
                                 "pred_object": objs[int(io_[j])],
                                 "conf": round(c, 3), "ok": bool(o_ok)})
    n = max(1, len(test))
    # 逐条预测落盘：θ 扫描只给聚合数，而「**校准在顶端翻转**」这类问题（首跑实测 θ≥0.95 的
    # 精度 82.3% 反而低于 θ≥0.9 的 94.4%）只能逐条看。不落盘就得为一个疑问重跑 14 分钟。
    pred_path = _EVAL / f"edge_nlu_preds_{lane}.jsonl"
    with pred_path.open("w", encoding="utf-8") as f:
        for r in rows_out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # θ 扫描：真概率的第一个用途——高置信子集的精度决定 θ_high 定在哪
    thetas = {}
    for th in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
        hi = [r for r in rows_out if r["conf"] >= th]
        thetas[str(th)] = {"kept": len(hi), "kept_frac": round(len(hi) / n, 4),
                           "precision": round(sum(r["ok"] for r in hi) / max(1, len(hi)), 4)}
    res = {"lane": lane, "train": len(train), "test": len(test),
           "domain_acc": round(dom_ok / n, 4), "object_acc": round(obj_ok / n, 4),
           "both_acc": round(both_ok / n, 4),
           "unseen_object_classes": unseen,
           "unseen_rows_in_test": sum(1 for r in test if r["object"] in unseen),
           "theta_scan": thetas,
           "worst_objects": sorted(
               ({"object": k, **v, "acc": round(v["ok"] / max(1, v["n"]), 3)}
                for k, v in per_obj.items() if v["n"] >= 8),
               key=lambda x: x["acc"])[:12],
           "train_seconds": round(time.time() - t0, 1)}
    print(f"  domain {res['domain_acc']:.1%}｜**object {res['object_acc']:.1%}**｜"
          f"both {res['both_acc']:.1%}（未见类占测试集 {res['unseen_rows_in_test']} 行）")
    for th, v in thetas.items():
        print(f"    θ≥{th}: 保留 {v['kept_frac']:.1%}，其中对 {v['precision']:.1%}")
    if args.export and lane == args.export_from:
        # **先存 checkpoint 再导出**：首版直接 export，而 torch 2.13 的 `torch.onnx.export`
        # 需要 onnxscript——缺包时抛在最后一步，**15 分钟的训练白烧**（模型只在进程内）。
        # 两道防线：这里落盘（可 --export-only 重试），以及 main() 在开跑前先验导出依赖。
        ckpt = Path(args.export) / f"checkpoint_{lane}.pt"
        ckpt.parent.mkdir(parents=True, exist_ok=True)
        import torch as _t
        _t.save({"state_dict": model.state_dict(), "domains": doms, "objects": objs}, ckpt)
        print(f"  → checkpoint {ckpt.name}（{ckpt.stat().st_size/1e6:.1f}MB，"
              f"导出失败可 --export-only 重试，不必重训）")
        export_onnx(model, tok, doms, objs, Path(args.export))
    return res


_PARITY_TEXTS = ["打开座椅加热", "把空调调到26度", "关闭强力前除雾", "明天天气怎么样",
                 "播FM87.5", "调低一点音量", "打开车道保持", "关掉氛围灯"]


def export_onnx(model, tok, doms, objs, out: Path) -> None:
    import torch
    out.mkdir(parents=True, exist_ok=True)
    model.eval()
    ids, mask = encode(tok, ["打开座椅加热"])
    onnx_path = out / "edge_nlu.onnx"
    torch.onnx.export(
        model, (ids, mask), str(onnx_path),
        input_names=["input_ids", "attention_mask"],
        output_names=["domain_logits", "object_logits"],
        dynamic_axes={"input_ids": {0: "batch"}, "attention_mask": {0: "batch"},
                      "domain_logits": {0: "batch"}, "object_logits": {0: "batch"}},
        opset_version=17)
    # **收成单文件**：torch>=2.6 的导出器对稍大的模型默认外置权重（40KB 的 .onnx + 35MB 的
    # .onnx.data）。两个文件能跑，但端侧的「缺文件即 disabled」判据只认 edge_nlu.onnx，
    # 少了 .data 会变成「文件都在却加载失败」——一个更难查的形态。35MB 单文件没有代价。
    try:
        import onnx
        m = onnx.load(str(onnx_path))
        onnx.save_model(m, str(onnx_path), save_as_external_data=False)
        for extra in out.glob("edge_nlu.onnx*.data"):
            extra.unlink()
        (out / "edge_nlu.onnx.data").unlink(missing_ok=True)
    except Exception as e:
        print(f"  ⚠ 外置权重合并失败（模型仍可用但是两个文件）：{e}")
    _assert_onnx_parity(model, tok, out, onnx_path)
    (out / "labels.json").write_text(json.dumps(
        {"domains": doms, "objects": objs, "max_len": MAX_LEN}, ensure_ascii=False,
        indent=2), encoding="utf-8")
    # **落 tokenizer 自己的 token→id 映射，不是拷 vocab.txt**：底座 vocab.txt 里有 3 个空行，
    # 「行号即 id」与 transformers 的加载结果整体错开 1（`打` 在第 2803 行、tokenizer 里是
    # 2802）——端侧照行号推词表就会每个 token 都喂错，而模型只会「变差」不会报错。
    # parity 测试当场抓到了这个。导出映射 = 训练侧与推理侧同源，不猜别人的加载器。
    (out / "vocab.json").write_text(
        json.dumps(tok.get_vocab(), ensure_ascii=False), encoding="utf-8")
    print(f"  → ONNX {onnx_path}（{onnx_path.stat().st_size/1e6:.1f}MB）"
          f" + labels.json + vocab.json（{len(tok.get_vocab())} token）")


def _assert_onnx_parity(model, tok, out: Path, onnx_path: Path) -> None:
    """导出后立刻用 onnxruntime 复算，与 torch 输出逐条比对。

    **不验就等于没导**：导出链路（decomposition → 图翻译 → 优化）任一环出错都可能给出一个
    「能加载、能推理、答案全错」的模型——本次导出日志里就有两条
    `Evaluation failed: cannot reshape array of size 1 into shape (1,32)`（优化器常量折叠
    在动态轴上失败）。同分词器 parity 一个道理：跨实现的等价性必须被断言，不能被假设。
    """
    import numpy as np
    import onnxruntime as ort
    import torch
    from orchestrator.edge.nlu import WordPiece      # 端侧那份分词器，一并验
    wp = WordPiece(tok.get_vocab())
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    bad = []
    for text in _PARITY_TEXTS:
        ids, mask = wp.encode(text, MAX_LEN)
        o = sess.run(None, {"input_ids": [ids], "attention_mask": [mask]})
        with torch.no_grad():
            t = model(torch.tensor([ids]), torch.tensor([mask]))
        for name, a, b in (("domain", o[0][0], t[0][0].numpy()),
                           ("object", o[1][0], t[1][0].numpy())):
            if int(np.argmax(a)) != int(np.argmax(b)):
                bad.append(f"{text}:{name} argmax {np.argmax(a)}≠{np.argmax(b)}")
            elif float(np.abs(a - b).max()) > 1e-2:
                bad.append(f"{text}:{name} logits 偏差 {np.abs(a - b).max():.3f}")
    if bad:
        raise SystemExit("✗ ONNX 与 torch 输出不一致，导出物不可用：" + "；".join(bad))
    print(f"  ✓ ONNX↔torch parity {len(_PARITY_TEXTS)}/{len(_PARITY_TEXTS)}"
          f"（含端侧纯 Python 分词器）")


def _export_only(out: Path, ckpt_name: str) -> int:
    """从 checkpoint 重新导出 ONNX——不重训。导出侧的问题（缺包/opset/算子）修一次试一次，
    没有理由为它反复烧 15 分钟训练。"""
    import torch
    from transformers import BertTokenizerFast
    path = Path(ckpt_name)
    if not path.is_file():
        path = out / ckpt_name
    if not path.is_file():
        print(f"✗ 找不到 checkpoint {ckpt_name}")
        return 1
    blob = torch.load(path, map_location="cpu", weights_only=False)
    doms, objs = blob["domains"], blob["objects"]
    model = build_model(len(doms), len(objs))
    model.load_state_dict(blob["state_dict"])
    export_onnx(model, BertTokenizerFast.from_pretrained(str(_BASE)), doms, objs, out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lane", choices=["transfer", "holdout", "both"], default="transfer")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--export", default="", help="导出 ONNX 到该目录（默认不导）")
    ap.add_argument("--export-only", default="",
                    help="从 checkpoint 重新导出 ONNX，不重训（如 checkpoint_holdout.pt）")
    ap.add_argument("--export-from", default="holdout",
                    help="用哪个车道的模型导出——**默认 holdout**：transfer 车道刻意只用规则"
                         "命中的数据训练（那是评测设计），上线的模型当然要用全部语料")
    args = ap.parse_args()

    if not (_BASE / "config.json").is_file():
        print(f"✗ 缺底座模型 {_BASE}——先跑 scripts/fetch-edge-nlu-base.sh|ps1")
        return 1
    if args.export:
        # **导出依赖在开跑前验**：torch 2.13 的 onnx 导出走 onnxscript，缺包会在训练结束的
        # 最后一步才抛——15 分钟白烧。这类「长任务的收尾依赖」必须前置检查。
        try:
            import onnxscript  # noqa: F401
        except ImportError:
            print("✗ 需要 onnxscript 才能导出 ONNX（torch>=2.6 的导出后端）："
                  "pip install onnxscript onnx。**先装再训**——它只在最后一步用到，"
                  "缺包会让整轮训练白烧。")
            return 1
    if args.export_only:
        return _export_only(Path(args.export or _OUT), args.export_only)
    rows = load_rows()
    hit = sum(r["rule_hit"] for r in rows)
    print(f"语料 {len(rows)} 条（规则命中 {hit} = {hit/len(rows):.1%}）")
    lanes = ["transfer", "holdout"] if args.lane == "both" else [args.lane]
    res = [run_lane(l, rows, args) for l in lanes]
    _EVAL.mkdir(parents=True, exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).isoformat(),
               "base_model": "iic/nlp_structbert_backbone_tiny_std",
               "corpus_rows": len(rows), "rule_hit": hit,
               "hyper": {"epochs": args.epochs, "batch_size": args.batch_size,
                         "lr": args.lr, "max_len": MAX_LEN, "seed": SEED},
               "toolchain": _toolchain(),
               "lanes": res}
    _REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    md = ["# 端侧 NLU 训练报告（M5 P3）", "",
          f"> 生成 {payload['generated']}　底座 {payload['base_model']}"
          f"（4 层/hidden 256/8.9M 参数）　语料 {len(rows)} 条（规则命中 {hit/len(rows):.1%}）", "",
          f"> 工具链 {payload['toolchain']}　超参 {payload['hyper']}"
          "　——**跨 run 比读数之前先对这一行**：同语料同种子同划分，仅 torch/transformers"
          "版本不同，实测 holdout object 可差 8.9 点（2026-08-04 实证，findings §9.5）。", "",
          "两个口径，**第二个才是决策依据**：随机 holdout 会因 paraphrase 泄漏被抬高，"
          "只当同分布上界；transfer（规则命中→规则漏判）直接回答「模型能不能接住规则接不住的」。",
          "", ("⚠ **本次只跑了 " + "、".join(r["lane"] for r in res) + " 车道**"
               "——另一车道的数见 RFC §5-P3a，或 `--lane both` 一次出两行。"
               "只看一行等于选一个自己喜欢的结论。" if len(res) < 2 else ""),
          "", "| 车道 | train | test | domain | **object** | both |", "|---|---|---|---|---|---|"]
    for r in res:
        md.append(f"| {r['lane']} | {r['train']} | {r['test']} | {r['domain_acc']:.1%} | "
                  f"**{r['object_acc']:.1%}** | {r['both_acc']:.1%} |")
    for r in res:
        md += ["", f"## {r['lane']} 车道 θ 扫描（真概率的第一个用途）", "",
               "| θ_high | 保留比例 | 保留子集里对的比例 |", "|---|---|---|"]
        for th, v in r["theta_scan"].items():
            md.append(f"| ≥{th} | {v['kept_frac']:.1%} | {v['precision']:.1%} |")
        if r["unseen_object_classes"]:
            md += ["", f"⚠ 训练集里**一条样本都没有**的 object {len(r['unseen_object_classes'])} 类"
                       f"（占测试集 {r['unseen_rows_in_test']} 行）——这些必然全错，"
                       f"是语料问题不是模型问题：{'、'.join(r['unseen_object_classes'][:12])}"]
        md += ["", "最差对象（测试集 ≥8 行）：" + "、".join(
            f"{w['object']} {w['acc']:.0%}" + ("（未见类）" if w["unseen"] else "")
            for w in r["worst_objects"])]
    _REPORT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n报告 → {_REPORT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
