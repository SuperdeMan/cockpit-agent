"""续问窗（唤醒后连续对话）受话判定评测（2026-10-10，设计 docs/design/2026-10-10-handsfree-followup-rejection.md §2.3 / §6）。

对照两份判定在同一批「续问窗里收到的话」上的表现：
  - light：现有轻量判定（`admission.admission_messages`，来源 voice_followup，单次）——续问窗专用判定落地前，规划之前出口用的那一份；
  - continuation：续问窗专用判定（`admission.judge_continuation`：判否再问一次，两次都否才拒）。
指标：正例（对助手说的）误拒率、负例（乘客对话 / 电话 / 广播 / 自言自语…）拦下率、判不出数、时延分位。判不出按受话算（同引擎 fail-open）。

两态（同 eval_rejection.py 的「离线 / --live」）：
  - 离线（无参，零网络，pytest 也跑）：语料结构、计数下限、开发集与留出集不重叠、留出集句子不出现在续问窗提示词里；
  - `--live`：直连厂商（缺省 MiniMax-M3 = 云端 minimax 档 @fast 的型号，关思考、温度 0、32 token），
    密钥只从根 `.env` 读、不打印；`--repeat`（缺省 3）、`--split dev|heldout|all`、`--judge light|continuation|both`、`--out` 写 JSON。
    这是读数工具不是门禁：模型有方差，单趟不当基线，读数登记在设计文档 §6（证据绑定 SHA 与日期）。

用法：
  python test/eval_followup_admission.py
  python test/eval_followup_admission.py --live --split heldout --judge both
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

try:                                   # Windows 控制台默认 GBK，强制 UTF-8（同 e2e_ws.py 惯例）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_CORPUS = {
    "dev": _ROOT / "test" / "eval_corpus" / "followup_admission_cases.yaml",
    "heldout": _ROOT / "test" / "eval_corpus" / "followup_admission_heldout.yaml",
}
_EXPECT = ("accept", "reject", "either")
#: 计数下限：低于它读数没有意义（单句方差会主导分布）
_MIN = {"dev": {"accept": 50, "reject": 30}, "heldout": {"accept": 40, "reject": 25}}
_PUNCT = re.compile(r"[，。、！？,.!?…\s「」]")


def load(split: str) -> dict:
    return yaml.safe_load(_CORPUS[split].read_text(encoding="utf-8"))


def _norm(text: str) -> str:
    return _PUNCT.sub("", str(text or ""))


def check_offline() -> list[str]:
    """尺子自检：返回问题清单（空 = 通过）。"""
    from orchestrator.cloud import admission
    errors: list[str] = []
    texts: dict[str, set[str]] = {}
    for split in _CORPUS:
        data = load(split)
        contexts = data.get("contexts") or {}
        counts = defaultdict(int)
        texts[split] = set()
        for i, case in enumerate(data.get("cases") or []):
            text, expect, ctx = case.get("text"), case.get("expect"), case.get("ctx")
            if not text or expect not in _EXPECT:
                errors.append(f"{split}#{i} 缺 text 或 expect 非法：{case}")
                continue
            if ctx not in contexts:
                errors.append(f"{split}#{i} ctx 不存在：{ctx!r}")
            if _norm(text) in texts[split]:
                errors.append(f"{split} 重复句：{text!r}")
            texts[split].add(_norm(text))
            counts[expect] += 1
        for expect, floor in _MIN[split].items():
            if counts[expect] < floor:
                errors.append(f"{split} {expect} 不足 {floor}（当前 {counts[expect]}）")
    overlap = texts["dev"] & texts["heldout"]
    if overlap:
        errors.append(f"开发集与留出集重叠：{sorted(overlap)}")
    # 留出：不能是提示词里引号括起来的示例，也不能是提示词的一段（单字太短，只比示例）
    system = admission.continuation_messages("")[0]["content"]
    examples = {_norm(e) for e in re.findall(r"「([^」]+)」", system)}
    prompt = _norm(system)
    leaked = sorted(t for t in texts["heldout"] if t in examples or (len(t) >= 4 and t in prompt))
    if leaked:
        errors.append(f"留出集句子出现在续问窗提示词里（不再是留出）：{leaked}")
    return errors


# ── --live ───────────────────────────────────────────────────────────────────

def _provider():
    from scripts.dev_stack_lib import read_root_env
    env = read_root_env(_ROOT, {"MINIMAX_API_KEY", "MINIMAX_BASE_URL"})
    key = env.get("MINIMAX_API_KEY") or os.getenv("MINIMAX_API_KEY", "")
    if not key:
        raise SystemExit("根 .env 缺 MINIMAX_API_KEY")
    sys.path.insert(0, str(_ROOT / "llm-gateway"))
    from providers import OpenAICompatibleProvider
    return OpenAICompatibleProvider(
        key, base_url=env.get("MINIMAX_BASE_URL") or "https://api.minimaxi.com/v1/chat/completions",
        auth_style="bearer", disable_thinking=True, token_param="max_completion_tokens", thinking_style="mimo",
        thinking_required_models=("MiniMax-M3.1-Flash-Preview",))


async def run_live(splits: list[str], judges: list[str], model: str, repeat: int, concurrency: int) -> list[dict]:
    from orchestrator.cloud import admission
    provider = _provider()
    gate = asyncio.Semaphore(concurrency)
    rows: list[dict] = []

    async def complete(messages):
        last = None
        for attempt in range(4):                      # 429 限流：退避重试；仍失败按判不出算
            try:
                raw, *_ = await provider.complete(messages, model, 0.0, 32, thinking=False, timeout_s=20)
                return raw
            except Exception as exc:                   # noqa: BLE001 - 读数工具：记下来、按判不出算
                last = exc
                await asyncio.sleep(4.0 * (attempt + 1))
        raise last

    async def one(split, case, contexts, judge, rep):
        previous = contexts.get(case["ctx"], "")
        async with gate:
            started = time.monotonic()
            if judge == "light":
                verdict = await admission.judge_addressed(complete, case["text"], previous, "voice_followup")
                votes = (verdict,)
            else:
                verdict, votes = await admission.judge_continuation(complete, case["text"], previous)
            rows.append({"split": split, "judge": judge, "rep": rep, "text": case["text"], "ctx": case["ctx"],
                         "expect": case["expect"], "tag": case.get("tag", ""), "verdict": verdict,
                         "votes": list(votes), "ms": round((time.monotonic() - started) * 1000)})

    jobs = []
    for split in splits:
        data = load(split)
        for case in data["cases"]:
            for judge in judges:
                for rep in range(repeat):
                    jobs.append(one(split, case, data["contexts"], judge, rep))
    await asyncio.gather(*jobs)
    return rows


def summarize(rows: list[dict]) -> dict:
    out: dict = {}
    for key in sorted({(r["split"], r["judge"]) for r in rows}):
        part = [r for r in rows if (r["split"], r["judge"]) == key]
        stat = {}
        for expect in _EXPECT:
            sub = [r for r in part if r["expect"] == expect]
            rejected = sum(1 for r in sub if r["verdict"] is False)
            stat[expect] = {"n": len(sub), "rejected": rejected}
        lat = sorted(r["ms"] for r in part)
        misses = defaultdict(lambda: [0, 0])
        for r in part:
            wrong = (r["expect"] == "accept" and r["verdict"] is False) or (
                r["expect"] == "reject" and r["verdict"] is not False)
            if r["expect"] != "either":
                misses[(r["expect"], r["text"])][0] += wrong
                misses[(r["expect"], r["text"])][1] += 1
        out["/".join(key)] = {
            "false_reject": f"{stat['accept']['rejected']}/{stat['accept']['n']}",
            "caught": f"{stat['reject']['rejected']}/{stat['reject']['n']}",
            "either_rejected": f"{stat['either']['rejected']}/{stat['either']['n']}",
            "unavailable": sum(1 for r in part if r["verdict"] is None),
            "latency_ms_p50_p90": [lat[len(lat) // 2], lat[int(len(lat) * 0.9)]] if lat else [],
            "misses": sorted(f"[{e}] {w}/{n} {t}" for (e, t), (w, n) in misses.items() if w),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--split", choices=("dev", "heldout", "all"), default="all")
    ap.add_argument("--judge", choices=("light", "continuation", "both"), default="continuation")
    ap.add_argument("--model", default="MiniMax-M3")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    errors = check_offline()
    if errors:
        print("尺子自检失败：")
        for e in errors:
            print("  -", e)
        return 1
    print("尺子自检通过：" + "，".join(f"{s} {len(load(s)['cases'])} 句" for s in _CORPUS))
    if not a.live:
        return 0
    splits = list(_CORPUS) if a.split == "all" else [a.split]
    judges = ["light", "continuation"] if a.judge == "both" else [a.judge]
    rows = asyncio.run(run_live(splits, judges, a.model, a.repeat, a.concurrency))
    report = {"model": a.model, "repeat": a.repeat, "results": summarize(rows)}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps({**report, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
