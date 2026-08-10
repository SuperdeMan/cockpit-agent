#!/usr/bin/env python
"""意图对抗 L0 门禁的**唯一入口**（本地与 CI 共用）。

为什么要有这个薄封装，而不是让每个人自己拼命令：

2026-08-10 发生过一次真实事故——除雾能力补齐时漏了对抗覆盖，`--strict` 实际
exit 2，人工用 `cmd | tail; echo $?` 读退出码，拿到的是 `tail` 的 0，于是一个红
门禁被报成了绿，直到下一提交才补上（findings §26.4）。同一个坑本项目此前已记过
`${PIPESTATUS[0]}`，还是又踩了一次。

根治办法不是「下次记得」，是让证据链上**不再出现 shell 管道**：由 Python 起
subprocess、直接读 returncode。今后人工报 L0 门禁读数，只允许引用这个入口的输出。

跑的两条都是 `--strict` 档：普通 `--list` 只**展示** coverage gap，`--strict` 才把
它升级成**阻断**——同一个门禁在两种模式下严厉程度不同，这是 CI 必须跑 strict 的理由。

L0 全离线：真实 Edge servicer + 真实检索，零 LLM、零网络、零费用。因此它符合
「live LLM gate 不阻断 PR」的既有边界——被阻断的只是确定性检查。

用法：
    python scripts/check_intent_gate.py                    # 本地
    python scripts/check_intent_gate.py --json <path>      # CI（落 artifact）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "test" / "eval_intent_adversarial.py"

# 每条 suite 单独指定 --out-json/--out-md：两条否则会写同一个默认文件，
# 后跑的那条把前一条的报告悄悄盖掉，CI artifact 里只剩半份证据。
SUITES = (
    ("discovery", "_ci-run-intent-adversarial-l0-discovery"),
    ("gate", "_ci-run-intent-adversarial-l0-gate"),
)

_SUMMARY_PREFIXES = ("units=", "[!]", "coverage", "strict")


def _summary_lines(stdout: str) -> list[str]:
    """摘出关键行。全文可能上千行，但判红之后人要看的就这几条。"""
    return [line for line in stdout.splitlines()
            if line.startswith(_SUMMARY_PREFIXES)]


def run_suite(suite: str, stem: str) -> dict:
    out_dir = ROOT / "docs" / "reviews" / "eval"
    cmd = [
        sys.executable, str(EVAL),
        "--suite", suite, "--layer", "l0", "--strict",
        "--out-json", str(out_dir / f"{stem}.json"),
        "--out-md", str(out_dir / f"{stem}.md"),
    ]
    # shell=False：不经 shell 就不存在管道，退出码原样拿到。
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    summary = _summary_lines(proc.stdout or "")
    print(f"── suite={suite} layer=l0 --strict → exit {proc.returncode}")
    for line in summary:
        print(f"   {line}")
    if proc.returncode != 0:
        # 失败时把 stderr 与全量 stdout 尾部打出来——strict 的失败信息本身可读，
        # 不需要额外解析报告内容。
        sys.stdout.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
    return {"suite": suite, "exit": proc.returncode, "summary": summary}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default="",
                    help="把 {suite: {exit, summary}} 落一份，供 CI artifact")
    args = ap.parse_args()

    results = [run_suite(suite, stem) for suite, stem in SUITES]
    failed = [row for row in results if row["exit"] != 0]

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {row["suite"]: {"exit": row["exit"], "summary": row["summary"]}
             for row in results}, ensure_ascii=False, indent=2), encoding="utf-8")

    if failed:
        print(f"\nFAIL: {len(failed)}/{len(results)} 条 L0 门禁未通过："
              f"{', '.join(row['suite'] for row in failed)}")
        return 1
    print(f"\nOK: {len(results)}/{len(results)} 条 L0 门禁通过（strict 档）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
