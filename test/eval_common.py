"""T3.4 评测报告 + 基线比对公共工具，供 eval_fast_intent.py / eval_route_hints.py 共用。

纯函数 + stdlib；不 import 任何被测业务模块，保持与"评测什么"完全解耦，方便未来
还有第三个 eval_*.py 复用。设计要点见 docs/design/2026-07-03-r3.4-intent-eval-baseline.md。
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class CaseResult:
    """一条评测用例的结果。id 是稳定 key（f"{bucket}::{text}"），供逐例基线 diff。"""

    id: str
    bucket: str
    text: str
    expected: object
    actual: object
    passed: bool
    detail: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = ""


def git_short_sha() -> str:
    """best-effort 取 HEAD 短 hash；非 git 环境/超时静默返回空串。"""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def build_report(subject: str, corpus_sources: list[dict], cases: list[CaseResult]) -> dict:
    """把 case 级结果聚合成可 json.dumps 的报告 dict：meta/corpus_sources/buckets/overall/cases。

    buckets 按分桶名分别统计（不做跨桶加权平均——多分类准确率和二元 guard-rail 通过率
    是两种不同性质的指标，混合会互相掩盖信号，见设计文档 §3.2）。
    """
    buckets: dict[str, dict] = {}
    for c in cases:
        bucket = buckets.setdefault(c.bucket, {"total": 0, "passed": 0, "failures": []})
        bucket["total"] += 1
        if c.passed:
            bucket["passed"] += 1
        else:
            bucket["failures"].append(asdict(c))
    for bucket in buckets.values():
        bucket["pass_rate"] = round(bucket["passed"] / bucket["total"], 4) if bucket["total"] else 0.0

    total = len(cases)
    passed = sum(1 for c in cases if c.passed)
    return {
        "meta": {
            "subject": subject,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "commit": git_short_sha(),
        },
        "corpus_sources": corpus_sources,
        "buckets": buckets,
        "overall": {
            "total": total,
            "passed": passed,
            "pass_rate": round(passed / total, 4) if total else 0.0,
        },
        # dict 而非 list：按 id 直接查找，供 diff_against_baseline 用；Python 3.7+ dict 保序，
        # render_markdown 仍可按插入顺序遍历。
        "cases": {c.id: asdict(c) for c in cases},
    }


def render_markdown(report: dict) -> str:
    """report dict -> Markdown：总览表 + 分桶细分 + 失败明细 + 数据来源 + 已知限制。"""
    meta = report["meta"]
    lines = [
        f"# 意图路由评测基线 — {meta['subject']}",
        "",
        f"生成时间：{meta['generated_at']}　commit：{meta['commit'] or '(unknown)'}",
        "",
        "## 总览",
        "| 分桶 | 总数 | 通过 | 通过率 |",
        "|---|---|---|---|",
    ]
    for name, bucket in report["buckets"].items():
        lines.append(f"| {name} | {bucket['total']} | {bucket['passed']} | {bucket['pass_rate'] * 100:.1f}% |")
    overall = report["overall"]
    lines.append(f"| **合计** | **{overall['total']}** | **{overall['passed']}** | **{overall['pass_rate'] * 100:.1f}%** |")
    lines.append("")

    lines.append("## 失败用例")
    failures = [c for c in report["cases"].values() if not c["passed"]]
    if not failures:
        lines.append("（当前基线：无失败）")
    else:
        for c in failures:
            detail = f"（{c['detail']}）" if c.get("detail") else ""
            lines.append(f"- [{c['bucket']}] `{c['text']}` — expected={c['expected']!r} actual={c['actual']!r}{detail}")
    lines.append("")

    lines.append("## 数据来源")
    lines.append("| 来源 | 用例数 |")
    lines.append("|---|---|")
    for src in report["corpus_sources"]:
        lines.append(f"| {src['path']} | {src['count']} |")
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict, md: str, json_path: Path, md_path: Path) -> None:
    json_path = Path(json_path)
    md_path = Path(md_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")


def load_baseline(path: Path) -> dict | None:
    """基线文件不存在返回 None（调用方据此提示"先 --write-baseline"）。"""
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class DiffResult:
    regressions: list[tuple[str, dict, dict]]
    improvements: list[tuple[str, dict, dict]]
    new_cases: list[str]
    removed_cases: list[str]

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)


def diff_against_baseline(current: dict, baseline: dict) -> DiffResult:
    """按 case id 精确比对 passed 布尔值——不用聚合百分比阈值。

    fast_intent/route_hints 都是纯规则引擎（不经 LLM），同代码同输入 100% 可复现：一个用例从
    基线里 pass 翻成这次 fail 就是唯一需要报警的硬信号，不会被"另一个用例同时 fail->pass
    抵消净变化"掩盖，也不需要模糊容差。见设计文档 §3.3。
    """
    cur_cases = current["cases"]
    base_cases = baseline["cases"]
    regressions: list[tuple[str, dict, dict]] = []
    improvements: list[tuple[str, dict, dict]] = []
    for cid, cc in cur_cases.items():
        bc = base_cases.get(cid)
        if bc is None:
            continue
        if bc["passed"] and not cc["passed"]:
            regressions.append((cid, bc, cc))
        elif not bc["passed"] and cc["passed"]:
            improvements.append((cid, bc, cc))
    new_cases = [cid for cid in cur_cases if cid not in base_cases]
    removed_cases = [cid for cid in base_cases if cid not in cur_cases]
    return DiffResult(regressions=regressions, improvements=improvements,
                       new_cases=new_cases, removed_cases=removed_cases)


def print_ci_annotations(subject: str, diff: DiffResult, baseline_path: Path) -> None:
    """regressions 非空时打印 ::warning:: GitHub Actions annotation；不 raise、不改 exit code——
    是否阻塞完全由调用方 main() 的 --strict 决定。"""
    if not diff.has_regressions:
        print(f"[{subject}] 无回归（基线：{baseline_path}）")
        return
    detail = "; ".join(
        f"{cid!r}(actual={cc['actual']!r})" for cid, _bc, cc in diff.regressions
    )
    print(f"::warning::{subject}: {len(diff.regressions)} case(s) regressed vs baseline — {detail}")


# ── 评测 LLM provider 锁定 + 漂移守卫（多模型运行时硬化 D8）──────────────────
# 治两次真实事故：llm-gateway 重建后 active 静默回落 env、评测中途 HMI 切 provider
# 产出混脑报告（canonical 重跑 @M3 与基线 @mimo 不可比）。设计：
# docs/design/2026-07-17-llm-runtime-hardening.md §4 D8。


class ProviderLock:
    """评测期 active LLM 锁定 + 漂移守卫。

    - ``pin()``：want 非空 → POST /api/llm/provider 钉住（失败抛 RuntimeError，锁定是
      硬要求）；want 空 → 记录当前 active 作基线（网关不可达则降级 locked=False、
      check 全跳过——mock 车道无网关也能跑）。
    - ``check(label)``：GET 复核 active；变了记一笔漂移（时点+前后值），并以新值续测
      （同一漂移不刷屏）。
    - ``summary()``：并进评测报告；``drift_detected=True`` 时驱动器应让退出码非零
      （报告作废重跑——评测期间切 provider 本来就该炸，这是特性）。
    """

    def __init__(self, base_url: str, want: str = "", model: str = "",
                 timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.want = (want or "").strip()
        self.model = (model or "").strip()
        self.timeout = timeout
        self.baseline = ""          # "provider:model"
        self.locked = False         # 显式 pin 成功
        self.available = True       # 网关可达；False 时 check 全跳过
        self.drifts: list[dict] = []

    # 独立方法便于单测注入替身（monkeypatch 实例 _http 即可，不起真 HTTP）。
    def _http(self, method: str, path: str, payload: dict | None = None) -> dict | None:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            return None

    def _active(self) -> str:
        st = self._http("GET", "/api/llm/providers")
        act = st.get("active") if isinstance(st, dict) else None
        if not isinstance(act, dict):
            return ""
        return f"{act.get('provider', '?')}:{act.get('model', '?')}"

    def pin(self) -> str:
        if self.want:
            body = {"provider": self.want}
            if self.model:
                body["model"] = self.model
            st = self._http("POST", "/api/llm/provider", body)
            if not isinstance(st, dict) or "active" not in st:
                raise RuntimeError(
                    f"ProviderLock: 钉住 {self.want} 失败（网关不可达或 provider 未配置）")
            self.locked = True
        cur = self._active()
        if not cur:
            self.available = False
            return "unknown"
        self.baseline = cur
        return cur

    def check(self, label: str = "") -> None:
        if not self.available or not self.baseline:
            return
        cur = self._active()
        if cur and cur != self.baseline:
            self.drifts.append({"at": label, "from": self.baseline, "to": cur})
            self.baseline = cur

    def summary(self) -> dict:
        return {"provider": self.baseline or "unknown", "locked": self.locked,
                "drift_detected": bool(self.drifts), "drifts": list(self.drifts)}
