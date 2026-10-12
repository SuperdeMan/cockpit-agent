"""条件指令前件判定评测（2026-10-12，设计 docs/design/2026-10-12-conditional-instruction-judgement.md §5）。

拿 `runtime.condition_judgement.judge_condition`（线上同一份提示词、解析与证据复核）去判语料里的「条件 × 观察」，
按成立 / 不成立 / 判不了三类统计。唯一的硬指标：**不成立与判不了被判成成立的次数必须为 0**——那是唯一会误执行的错。

两态（同 eval_followup_admission.py）：
  - 离线（无参，零网络，pytest 也跑）：语料结构、每类计数下限、id 唯一；
  - `--live`：直连厂商（缺省 MiniMax-M3 = 云端主档的缺省型号，关思考、温度 0、256 token），密钥只从根 `.env` 读、不打印；
    `--model` 可给多个（逗号分隔）、`--repeat`（缺省 3）、`--out` 写 JSON。读数工具不是门禁：读数登记在设计文档 §7（绑定 SHA 与日期）。

用法：
  python test/eval_condition_judgement.py
  python test/eval_condition_judgement.py --live --model MiniMax-M3,MiniMax-M3.1-Flash-Preview
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import yaml

try:                                   # Windows 控制台默认 GBK，强制 UTF-8（同 e2e_ws.py 惯例）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from runtime import condition_judgement as cj  # noqa: E402

_CORPUS = _ROOT / "test" / "eval_corpus" / "condition_judgement_cases.yaml"
#: 每类计数下限：低于它读数没有意义
_MIN = {cj.HOLDS: 10, cj.FAILS: 10, cj.UNKNOWN: 10}


def load() -> dict:
    return yaml.safe_load(_CORPUS.read_text(encoding="utf-8"))


def observations_of(case: dict) -> list[dict]:
    """语料里的观察 → 运行时观察的形状（status=ok；第三方标 untrusted）。"""
    out = []
    for i, obs in enumerate(case.get("observations") or []):
        row = {"step_id": f"s{i + 1}", "status": "ok", "intent": obs.get("intent", ""),
               "speech": obs.get("speech", ""), "data": obs.get("data") or {}}
        if obs.get("untrusted"):
            row["untrusted"] = True
        out.append(row)
    return out


def check_offline() -> list[str]:
    """尺子自检：返回问题清单（空 = 通过）。"""
    errors: list[str] = []
    data = load()
    if not str(data.get("now") or "").strip():
        errors.append("缺 now（判定用的现在时间）")
    cases = data.get("cases") or []
    ids = [c.get("id") for c in cases]
    if len(ids) != len(set(ids)):
        errors.append("id 重复")
    for case in cases:
        if case.get("expect") not in cj.VERDICTS:
            errors.append(f"{case.get('id')}: expect 不在三值里")
        if not str(case.get("condition") or "").strip():
            errors.append(f"{case.get('id')}: 缺 condition")
        observations = case.get("observations") or []
        if not observations or any(not str(o.get("speech") or "").strip() or not o.get("intent") for o in observations):
            errors.append(f"{case.get('id')}: 观察缺 intent / speech")
    for verdict, minimum in _MIN.items():
        n = sum(1 for c in cases if c.get("expect") == verdict)
        if n < minimum:
            errors.append(f"{verdict} 只有 {n} 条（下限 {minimum}）")
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


async def run_live(models: list[str], repeat: int, concurrency: int) -> list[dict]:
    provider = _provider()
    data = load()
    gate = asyncio.Semaphore(concurrency)
    rows: list[dict] = []

    async def one(model: str, case: dict, rep: int):
        async def complete(messages):
            last = None
            for attempt in range(4):                  # 429 限流：退避重试；仍失败按判不了算（同线上：异常 ⇒ unknown）
                try:
                    raw, *_ = await provider.complete(messages, model, 0.0, 256, thinking=False, timeout_s=20)
                    return raw
                except Exception as exc:               # noqa: BLE001 - 读数工具：记下来
                    last = exc
                    await asyncio.sleep(4.0 * (attempt + 1))
            raise last

        async with gate:
            started = time.monotonic()
            verdict, evidence = await cj.judge_condition(complete, case["condition"], observations_of(case), data["now"])
            rows.append({"model": model, "rep": rep, "id": case["id"], "expect": case["expect"],
                         "verdict": verdict, "evidence": evidence, "ms": round((time.monotonic() - started) * 1000)})

    await asyncio.gather(*(one(m, c, r) for m in models for c in data["cases"] for r in range(repeat)))
    return rows


def summarize(rows: list[dict]) -> dict:
    out: dict = {}
    for model in sorted({r["model"] for r in rows}):
        part = [r for r in rows if r["model"] == model]
        by_expect = {}
        for expect in sorted(cj.VERDICTS):
            sub = [r for r in part if r["expect"] == expect]
            by_expect[expect] = {"n": len(sub), "exact": sum(1 for r in sub if r["verdict"] == expect),
                                 "judged_holds": sum(1 for r in sub if r["verdict"] == cj.HOLDS)}
        false_holds = sorted({f"{r['id']}→{r['evidence']}" for r in part
                              if r["expect"] != cj.HOLDS and r["verdict"] == cj.HOLDS})
        misses = sorted({f"{r['id']} 期望 {r['expect']} 得 {r['verdict']}" for r in part if r["verdict"] != r["expect"]})
        lat = sorted(r["ms"] for r in part)
        out[model] = {
            "per_expect": by_expect,
            "false_holds": false_holds,
            "latency_ms_p50_p90": [lat[len(lat) // 2], lat[int(len(lat) * 0.9)]] if lat else [],
            "misses": misses,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true")
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
    print(f"尺子自检通过：{len(load()['cases'])} 条")
    if not a.live:
        return 0
    models = [m.strip() for m in a.model.split(",") if m.strip()]
    rows = asyncio.run(run_live(models, a.repeat, a.concurrency))
    report = {"models": models, "repeat": a.repeat, "results": summarize(rows)}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps({**report, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
