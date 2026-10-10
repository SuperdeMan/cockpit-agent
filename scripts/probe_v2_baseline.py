"""CA2-01/JV00: frozen, synthetic, zero-execution baseline for the v2 migration.

Reuses the established signed identity, WS turn, trace-settling and release probes.
The read-only lane never confirms an operation or restores a changed vehicle: an unexpected
action or vehicle difference stops the run. The write lanes (core journey freeze §12, user's
long-term authorization 2026-10-09) confirm only declared turns, write only this run's
synthetic users or the simulated vehicle v1, and must clean up and verify afterwards.
Raw artifacts stay in .artifacts, not in Git.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from scripts import probe_history_window as identity
from scripts import probe_qa_regression as wire
from scripts import probe_qa_long_sessions as audit
from scripts.e2e_identity import sign_identity
from scripts.journey_expect import EXPECT_KEYS as JOURNEY_EXPECT_KEYS, check_expect as journey_check
from scripts.render_cloud_env import DEMO_AUTH_SCOPES

CORPUS = ROOT / "test/eval_corpus/v2_runtime/seed.yaml"
#: 冻结要求「已提交、干净」的范围：这次跑数真正读进来的东西——运行器与它导入的 scripts/、runtime/，语料，判定读的来源证据表。
#: 资产树从发布 SHA 读（`git rev-parse <sha>:<path>`），不读工作树。主仓库是多个会话共享的工作树，别处未提交的文档不影响这次跑数，
#: 却曾挡住基线（2026-10-09：一份 Android 记录、一份可观测台 brief）。新加的导入或读取的文件要落在这里面（测试钉着）。
FREEZE_INPUTS = ("scripts", "runtime", "test/eval_corpus", "test/journeys",
                 "agents/manual_rag/resources/source_evidence.yaml")
#: 输入目录里的测试不进跑数（运行器不导入它们）：别的会话常在改它们（2026-10-09 P1 跑数期间，容量回收与保留策略的测试改了一半），
#: 不该判成「跑数输入变了」。
FREEZE_EXCLUDES = ("scripts/tests", "runtime/tests")
_FREEZE_PATHSPEC = FREEZE_INPUTS + tuple(f":(exclude){p}" for p in FREEZE_EXCLUDES)
#: 核心旅程冻结清单（docs/design/2026-10-09-v2-core-journey-freeze.md）：只登记引用与分类，旅程内容在 source 指向的文件里。
#: 7 月旅程（test/journeys/）也是跑数输入——清单引用它们时一并进 `FREEZE_INPUTS`。
MANIFEST = ROOT / "test/eval_corpus/v2_runtime/core/manifest.yaml"
FAMILIES = {f"F{n:02d}" for n in range(1, 13)}
LANES = ("read_only", "synthetic_writes", "simulated_vehicle")
#: 写车道（冻结设计 §12）：R1 只写本次运行的合成签名用户，R2 只动模拟车 v1。用户 2026-10-09 给了长期授权（§9），每次跑数把授权记录写进报告。
WRITE_LANES = ("synthetic_writes", "simulated_vehicle")
LANE_AUTHORIZATION = {
    "basis": "docs/design/2026-10-09-v2-core-journey-freeze.md §9：用户 2026-10-09「P3给长期授权」",
    "scope": "本次运行的合成签名用户（R1）；模拟车 v1（R2）",
}
#: R1 收尾在云端容器里删、回读为零的数据类 → compose 服务名（程序见 `_DATA_CLEANUP_PROGRAMS`）
DATA_STORES = {"memory": "memory", "scene": "scene-orchestrator-agent", "reminder": "reminder-agent"}
#: 本次运行的合成用户 id：`e2e-v2-<12 位十六进制>-<旅程>-r<遍>`；清理只认这个形状且以本次 run 打头
_RUN_ID_RE = re.compile(r"e2e-v2-[0-9a-f]{12}")
_SYNTHETIC_USER_RE = re.compile(r"e2e-v2-[0-9a-f]{12}-[a-z0-9-]+-r\d+")
_AFFIRMATION_RE = re.compile(r"(?:确认|好的|可以|同意|执行)[。！!\s]*")
#: 7 月旅程在 v2 运行器里认的原语：一轮只有 `say` + `expect`，前置只有 `setup.location`；别的记进 `unsupported`，跑数时记「未测」
_JOURNEY_TURN_KEYS = {"say", "expect"}
#: 条目级 meta 只认这些键：F12 受话与拒识要按语音来源发（`voice_*` / `ptt`，与编排 `is_voice_input_source` 同一口径）。
#: 位置走 7 月旅程的 `setup.location`；别的键一律拒绝——meta 是下发给编排的，不许语料随手塞别的东西。
_CASE_META_KEYS = {"input_source"}
SCOPES = tuple(s for s in DEMO_AUTH_SCOPES if s not in {"merchant.write", "payment.invoke"})
#: 清单条目可以按旅程补的权限（冻结 P2，2026-10-10）：周边 / 停车 Agent 按整个 Agent 要求 `payment.invoke`（只为预留的下单桩），
#: 不给就连只读的搜索都被拒。只收这一项、只给只读车道——只读车道从不确认（`validate_case` 钉着），支付一步都执行不了；商户写永远不给。
SCOPE_EXTRAS = ("payment.invoke",)
# Scene admission is currently Agent-wide (including media/navigation/profile scopes).
# Keep ordinary capability visibility comparable; no transaction permissions or confirmations.
ASSET_GROUPS = {
    "protocol": ("proto",), "planning": ("orchestrator/cloud", "skills"),
    "capabilities": ("agents", "orchestrator/edge/knowledge"),
    "policy": ("runtime", "security"), "gateway": ("llm-gateway", "gateway"),
    "manual_catalog": ("agents/manual_rag/resources",),
}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8").strip()


def digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cases(corpus: Path = CORPUS) -> list[dict]:
    data = yaml.safe_load(corpus.read_text(encoding="utf-8"))
    cases = data["cases"]
    if data.get("version") != 1 or data.get("split") != "regression":
        raise ValueError("expected versioned regression corpus")
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids) or not cases:
        raise ValueError("duplicate/empty cases")
    for case in cases:
        validate_case(case)
    return cases


def validate_case(case: dict) -> None:
    """冻结语料的静态护栏（v2 语料与清单引用的 7 月旅程同一份）：只读车道从不确认、取消只说「取消」、不许肯定答复。
    写车道（§12）的确认只能是旅程声明的那一轮：`confirm: true`、原话只说「确认」、紧跟一轮期望挂起确认；
    R2 旅程声明自己可能改动的车态键与收尾话轮，R1 旅程声明要清理核对的数据类。"""
    if not case.get("family") or not case.get("turns"):
        raise ValueError("case requires family and turns")
    meta = case.get("meta") or {}
    if set(meta) - _CASE_META_KEYS:
        raise ValueError("case meta only takes input_source")
    source = str(meta.get("input_source") or "")
    if "input_source" in meta and not (source.startswith("voice_") or source == "ptt"):
        raise ValueError("input_source must be a voice source (voice_* or ptt)")
    lane = case.get("lane") or "read_only"
    if lane not in LANES:
        raise ValueError("unknown lane")
    write = lane in WRITE_LANES
    previous: dict = {}
    for turn in case["turns"]:
        if turn.get("is_confirmation"):
            raise ValueError("declare a confirmation turn with confirm: true")
        if turn.get("confirm"):
            if not write:
                raise ValueError("baseline must never confirm")
            if turn.get("say") != "确认" or not (previous.get("expect") or {}).get("need_confirm"):
                raise ValueError("a confirmation turn says exactly 确认, right after a turn that expects need_confirm")
        else:
            if turn.get("cancel_pending") and turn["say"] != "取消":
                raise ValueError("cleanup must use an exact addressed cancellation")
            if _AFFIRMATION_RE.fullmatch(str(turn.get("say") or "")):
                raise ValueError("affirmation is not allowed outside a declared confirmation turn")
        previous = turn
    if lane == "simulated_vehicle":
        keys = case.get("vehicle_keys")
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k for k in keys):
            raise ValueError("simulated_vehicle journeys declare the vehicle_keys they may change")
        cleanup = case.get("cleanup") or []
        if not isinstance(cleanup, list):
            raise ValueError("cleanup is a list of turns")
        for turn in cleanup:
            if (not isinstance(turn, dict) or set(turn) - {"say", "confirm"} or not str(turn.get("say") or "").strip()
                    or _AFFIRMATION_RE.fullmatch(str(turn["say"]))):
                raise ValueError("cleanup turns are {say, confirm?}; a needed confirmation is declared, not said")
    elif case.get("vehicle_keys") or case.get("cleanup"):
        raise ValueError("only simulated_vehicle journeys take vehicle_keys / cleanup")
    if lane == "synthetic_writes":
        stores = case.get("data_cleanup")
        if not isinstance(stores, list) or not stores or set(stores) - set(DATA_STORES):
            raise ValueError("synthetic_writes journeys declare data_cleanup from memory / scene / reminder")
    elif case.get("data_cleanup"):
        raise ValueError("only synthetic_writes journeys take data_cleanup")
    extras = case.get("scopes_extra") or []
    if extras:
        if not isinstance(extras, list) or set(extras) - set(SCOPE_EXTRAS):
            raise ValueError("scopes_extra only takes " + ", ".join(SCOPE_EXTRAS))
        if lane != "read_only":
            raise ValueError("scopes_extra is only for read_only journeys (they never confirm)")


def _adapt_journey(journey: dict) -> dict:
    """7 月旅程 → v2 case：一轮 `say` + `expect`（判据键由 `scripts.journey_expect` 认），前置位置覆盖这次会话的当前位置；
    其余原语（确认、按钮、等推送、车态前置、收尾……）记进 `unsupported`，跑数时整条记「未测」。`requires` 只记依赖的外部服务。"""
    setup = journey.get("setup") or {}
    unsupported = {f"setup.{k}" for k in setup if k != "location"}
    unsupported |= {k for k in ("final_vehicle", "cleanup") if journey.get(k)}
    for turn in journey.get("turns") or []:
        unsupported |= {k for k in turn if k not in _JOURNEY_TURN_KEYS}
        for branch in [turn.get("expect") or {}, *((turn.get("expect") or {}).get("any_of") or [])]:
            if "process_min" in branch:
                unsupported.add("expect.process_min")      # v2 收发层不采过程区事件
    case = {"id": journey["id"], "family": "", "source_kind": "journey",
            "turns": [{"say": str(t["say"]), "expect": dict(t.get("expect") or {})}
                      for t in journey.get("turns") or [] if "say" in t],
            "providers": sorted({p for r in journey.get("requires") or [] for p in str(r).split("|")})}
    location = setup.get("location")
    if isinstance(location, dict) and "lat" in location and "lng" in location:
        case["location"] = {"lat": str(location["lat"]), "lng": str(location["lng"])}
    if unsupported:
        case["unsupported"] = sorted(unsupported)
    return case


def load_manifest(path: Path = MANIFEST) -> list[dict]:
    """按清单逐条取旅程：来源是 v2 语料（原 schema）或 7 月旅程（经 `_adapt_journey`），分类以清单为准。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("split") != "regression":
        raise ValueError("expected versioned regression manifest")
    entries = data.get("journeys") or []
    ids = [e.get("id") for e in entries]
    if not entries or len(set(ids)) != len(ids):
        raise ValueError("duplicate/empty journeys")
    test_root = (ROOT / "test").resolve()
    docs: dict[Path, dict] = {}
    cases = []
    for entry in entries:
        if entry.get("family") not in FAMILIES or entry.get("lane") not in LANES:
            raise ValueError("journey requires a known family and lane")
        source = (test_root / str(entry.get("source") or "")).resolve()
        if test_root not in source.parents or not source.is_file():
            raise ValueError("journey source must be a file under test/")
        doc = docs.setdefault(source, yaml.safe_load(source.read_text(encoding="utf-8")))
        if "cases" in doc:
            found = [c for c in doc["cases"] if c.get("id") == entry["id"]]
            case = json.loads(json.dumps(found[0], ensure_ascii=False)) if len(found) == 1 else None
            if case is not None:
                case["source_family"] = case.get("family", "")
        elif "journeys" in doc:
            found = [j for j in doc["journeys"] if j.get("id") == entry["id"]]
            case = _adapt_journey(found[0]) if len(found) == 1 else None
        else:
            raise ValueError("unknown journey source schema")
        if case is None:
            raise ValueError("journey id not found exactly once in its source")
        case.update(family=entry["family"], lane=entry["lane"], source=str(entry["source"]),
                    safety=str(entry.get("safety") or ""), known_red=str(entry.get("known_red") or ""))
        if entry.get("scopes_extra"):
            case["scopes_extra"] = list(entry["scopes_extra"])
        validate_case(case)
        cases.append(case)
    return cases


def freeze(expected_sha: str, provider: str, model: str, corpus: Path = CORPUS, loader=None) -> dict:
    loader = loader or load_cases
    if not re.fullmatch(r"[a-f0-9]{40}", expected_sha):
        raise ValueError("expected release must be a full SHA")
    if _git("status", "--porcelain", "--", *_FREEZE_PATHSPEC):
        raise ValueError("freeze requires committed, clean inputs")
    corpus = corpus.resolve()
    relative_corpus = corpus.relative_to(ROOT).as_posix()
    _git("ls-files", "--error-unmatch", "--", relative_corpus)
    trees = {name: {p: _git("rev-parse", f"{expected_sha}:{p}") for p in paths}
             for name, paths in ASSET_GROUPS.items()}
    return {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "runner_sha": _git("rev-parse", "HEAD"), "release_sha": expected_sha,
        "chat": {"provider": provider, "model": model},
        "decision": {"status": "not_implemented", "model": None, "rubric": None},
        "apk": {"status": "not_measured"},
        "runtime_config": {"status": "not_attested", "note": "no secrets exported"},
        "split": "regression", "corpus_path": relative_corpus,
        "corpus_sha256": digest(loader(corpus)),
        "committed_asset_tree_oids": trees, "assets_sha256": digest(trees),
        "private_manual_package": "approved hash in pinned manual_catalog; no package exported",
        "scopes": list(SCOPES), "vehicle": "v1", "cases": len(loader(corpus)),
        "metrics": ["no_actions", "vehicle_unchanged", "manual_dispatched",
                    "manual_presented", "pending_addressed", "answer_terms",
                    "request_to_final_ms", "actual_model", "provider_usage"],
    }


def judge(expect: dict, obs: dict, detail: dict, *, query: str = "", elapsed_s: float = 0.0,
          lane: str = "read_only", vehicle_after: dict | None = None) -> dict:
    """Separate planning/dispatch from presentation; a spoken hint is not a card.

    写车道（§12）：动作只能是这一轮声明的 `actions_allowed`，`actions_required` 必须都出现，
    `vehicle_values` 是这一轮之后车态应有的值；只读车道出现任何动作都是失败。"""
    spans = detail.get("spans") or []
    attrs = [s.get("attrs") or {} for s in spans if isinstance(s, dict)]
    intents = set(str((detail.get("turn") or {}).get("intents") or "").split(","))
    for a in attrs:
        if isinstance(a, dict):
            intents.update(str(a.get("intents") or a.get("intent") or "").split(","))
    # A planner listing an intent is not proof that the corresponding Agent ran.
    manual_dispatched = any(
        isinstance(a, dict) and (a.get("agent_id") or a.get("agent")) == "manual-rag"
        for a in attrs)
    try:
        card = json.loads(obs.get("card_text") or "{}")
    except (TypeError, ValueError):
        card = {}
    def has_manual(c):
        return isinstance(c, dict) and (c.get("type") == "manual" or any(
            has_manual(x) for x in c.get("items", []) if isinstance(x, dict)))
    failures = []
    actions = [str(a) for a in obs.get("actions") or []]
    if lane in WRITE_LANES:
        allowed = {str(a) for a in expect.get("actions_allowed") or []}
        failures.extend("undeclared_action:" + a for a in actions if a not in allowed)
        failures.extend("action_missing:" + str(a) for a in expect.get("actions_required") or [] if str(a) not in actions)
        for key, wanted in (expect.get("vehicle_values") or {}).items():
            if not audit._vehicle_value_matches((vehicle_after or {}).get(key), wanted):
                failures.append("vehicle_value:" + str(key))
    elif actions:
        failures.append("unexpected_action")
    if obs.get("error") or "Agent 内部错误" in str(obs.get("speech") or ""):
        failures.append("technical_failure")
    if expect.get("need_confirm") and not (obs.get("need_confirm") and obs.get("operation_id")):
        failures.append("pending_missing")
    if obs.get("need_confirm") and not expect.get("need_confirm"):
        failures.append("unexpected_confirmation")
    if expect.get("manual"):
        if not manual_dispatched:
            failures.append("manual_not_dispatched")
        if not has_manual(card):
            failures.append("manual_not_presented")
    for term in expect.get("answer_terms") or []:
        if term not in str(obs.get("speech") or ""):
            failures.append("answer_missing:" + term)
    if expect.get("source_evidence"):
        failures.extend(judge_source_evidence(query, obs, detail, card))
    images = {image.get("asset_id") for c in manual_cards(card) for image in c.get("images", [])}
    for asset_id in expect.get("image_assets") or []:
        if asset_id not in images:
            failures.append("manual_image_missing:" + asset_id)
    # 7 月旅程的判据键（`scripts.journey_expect` 唯一实现）；确认挂起仍由上面那条判（它还要求带 operation_id，更严），不重复报
    shared = {k: v for k, v in expect.items() if k in JOURNEY_EXPECT_KEYS and k != "need_confirm"}
    if shared:
        view = _JourneyView({"speech": obs.get("speech") or "", "ui_card": card,
                             "need_confirm": bool(obs.get("need_confirm")), "follow_up": obs.get("follow_up") or ""},
                            list(obs.get("actions") or []), elapsed_s)
        failures.extend("journey:" + f for f in journey_check(shared, view, True, ()))
    return {"failures": failures, "manual_dispatched": manual_dispatched,
            "manual_presented": has_manual(card), "intents": sorted(intents - {""})}


class _JourneyView:
    """喂给 `journey_check` 的一轮观测：v2 收发层不采过程区事件；车况判据只在模拟车车道，这里不给读取函数。"""

    def __init__(self, final: dict, actions: list, elapsed: float) -> None:
        self.final, self.actions, self.process_events, self.elapsed = final, actions, [], elapsed


SOURCE_EVIDENCE = ROOT / "agents/manual_rag/resources/source_evidence.yaml"


def manual_cards(node: dict) -> list[dict]:
    if not isinstance(node, dict):
        return []
    return ([node] if node.get("type") == "manual" else []) + [
        child for item in node.get("items", []) for child in manual_cards(item)]


def judge_source_evidence(query: str, obs: dict, detail: dict, card: dict) -> list[str]:
    """Rebuild pinned source slices; keywords or numeric membership are insufficient."""
    from agents.manual_rag.src.source_evidence import load_evidence_guards
    manual = manual_cards(card)
    if len(manual) != 1 or manual[0].get("_prov", {}).get("mode") != "real":
        return ["source_evidence_card_missing"]
    try:
        item = manual[0]
        pages = {c["page_start"]: c["content"] for c in item.get("chunks", [])}
        guards = load_evidence_guards(SOURCE_EVIDENCE, item.get("document", {}), pages)
        guard = next(g for g in guards if g.matches(query))
        if not guard.available:
            return ["source_evidence_unverifiable"]
        parts = guard.select(query)
        speech = re.sub(r"\s+", "", str(obs.get("speech") or ""))
        failures = ["source_condition_lost:" + part.part_id for part in parts
                    if re.sub(r"\s+", "", part.text.strip()) not in speech]
    except (ValueError, KeyError, TypeError, StopIteration):
        return ["source_evidence_unverifiable"]
    if any(call.get("caller") == "manual-rag" for call in detail.get("llm_calls", [])):
        failures.append("source_evidence_generated")
    return failures


def vehicle_restore_diff(baseline: dict, final: dict) -> list[str]:
    """R2 收尾后的逐键核对：与跑前快照不同的键（任何一键不同即判失败）。"""
    return sorted(k for k in set(baseline) | set(final)
                  if not audit._vehicle_value_matches(final.get(k), baseline.get(k)))


def data_leftovers(result: dict, stores: list[str]) -> dict:
    """R1 清理回读：每类数据删后剩多少、或为什么没能核对。没有回读结果也算没清干净。"""
    out = {}
    for store in stores:
        r = result.get(store)
        if not isinstance(r, dict):
            out[store] = "no_result"
        elif r.get("error"):
            out[store] = "error:" + str(r["error"])[:60]
        elif not isinstance(r.get("left"), int) or isinstance(r.get("left"), bool):
            out[store] = "no_count"
        elif r["left"]:
            out[store] = r["left"]
    return out


#: 每类数据在自己的容器里删、回读：只认 argv[1] 给的那一个用户 id，只打印计数。
_DATA_CLEANUP_PROGRAMS = {
    "memory": r'''
import asyncio, json, os, sys
import grpc
sys.path.insert(0, "/app/gen/python")
from cockpit.memory.v1 import memory_pb2 as m, memory_pb2_grpc
USER = sys.argv[1]


async def main():
    async with grpc.aio.insecure_channel("127.0.0.1:" + os.getenv("MEMORY_PORT", "50053")) as channel:
        stub = memory_pb2_grpc.MemoryStub(channel)
        ok = (await stub.ForgetUser(m.ForgetUserRequest(user_id=USER), timeout=10)).ok
        data = json.loads((await stub.ExportUser(m.ExportUserRequest(user_id=USER), timeout=10)).json or "{}")
    left = (len(data.get("memories") or []) + len(data.get("relations") or [])
            + sum(1 for v in (data.get("profile") or {}).values() if v))
    print(json.dumps({"done": bool(ok), "left": left}))


asyncio.run(main())
''',
    "scene": r'''
import asyncio, json, sys
sys.path.insert(0, "/app")
from agents.scene_orchestrator.src.store import DISABLED, ENABLED, SceneStore
USER = sys.argv[1]


async def main():
    store = SceneStore()
    if not await store.init() or not store.pg_ok:
        print(json.dumps({"error": "store_unavailable"}))
        return
    scenes = await store.list(USER, statuses=(ENABLED, DISABLED))
    done = sum([await store.delete(USER, s.id) for s in scenes])
    left = len(await store.list(USER, statuses=(ENABLED, DISABLED)))
    print(json.dumps({"done": done, "left": left}))


asyncio.run(main())
''',
    "reminder": r'''
import asyncio, json, sys
sys.path.insert(0, "/app")
from agents.reminder.src.store import ACTIVE, ReminderStore
USER = sys.argv[1]


async def main():
    store = ReminderStore()
    if not await store.init() or not store.pg_ok:
        print(json.dumps({"error": "store_unavailable"}))
        return
    async with store._pool.acquire() as conn:
        occupants = [r["occupant_id"] for r in await conn.fetch(
            "SELECT DISTINCT occupant_id FROM reminder_item WHERE user_id=$1", USER)]
    done = 0
    for occupant in occupants:
        done += await store.cancel_all(USER, occupant_id=occupant)
    async with store._pool.acquire() as conn:
        left = await conn.fetchval(
            "SELECT COUNT(*) FROM reminder_item WHERE user_id=$1 AND status=ANY($2)", USER, list(ACTIVE))
    print(json.dumps({"done": done, "left": int(left or 0)}))


asyncio.run(main())
''',
}

#: 远端驱动：按 compose 服务标签找到唯一容器，载荷走 stdin（不进 argv），只回传每类的计数。
_DATA_CLEANUP_REMOTE = r'''
import json, subprocess, sys
programs = __PROGRAMS__
user = sys.argv[1]
out = {}
for store, (service, payload) in programs.items():
    ids = subprocess.check_output(["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=" + service],
                                  timeout=20).decode().split()
    if len(ids) != 1:
        out[store] = {"error": "containers:%d" % len(ids)}
        continue
    try:
        lines = subprocess.check_output(["docker", "exec", "-i", ids[0], "python", "-", user],
                                        input=payload.encode(), timeout=60).decode().strip().splitlines()
        out[store] = json.loads(lines[-1]) if lines else {"error": "no_output"}
    except Exception as exc:
        out[store] = {"error": type(exc).__name__}
print(json.dumps(out))
'''


def _ssh_config():
    import os
    from scripts.cloud_release_lib import SshConfig
    return SshConfig(os.environ["CAR_AGENT_DEPLOY_HOST"], os.environ.get("CAR_AGENT_DEPLOY_USER", "ubuntu"),
                     Path(os.environ["CAR_AGENT_SSH_IDENTITY"]), os.environ.get("CAR_AGENT_SSH_KEX_ALGORITHMS"))


def cleanup_synthetic_data(user: str, run_id: str, stores: list[str], *, runner=None, ssh=None) -> dict:
    """R1 收尾：在云端容器里删本次运行这个合成用户的数据并回读（§12.1）。不是本次 run 的合成用户一律拒绝。"""
    if (not _RUN_ID_RE.fullmatch(run_id) or not _SYNTHETIC_USER_RE.fullmatch(user)
            or not user.startswith(run_id + "-")):
        raise ValueError("cleanup only touches this run's synthetic users")
    if not stores or set(stores) - set(DATA_STORES):
        raise ValueError("unknown data store")
    programs = {store: (DATA_STORES[store], _DATA_CLEANUP_PROGRAMS[store]) for store in stores}
    remote = _DATA_CLEANUP_REMOTE.replace("__PROGRAMS__", repr(programs))
    argv = (ssh or _ssh_config()).ssh_argv("sudo python3 - " + user)
    proc = (runner or subprocess.run)(argv, input=remote.encode(), capture_output=True, timeout=300)
    lines = proc.stdout.decode("utf-8", "replace").strip().splitlines() if proc.returncode == 0 else []
    try:
        return json.loads(lines[-1]) if lines else {}
    except ValueError:
        return {}


def _redact(value):
    if isinstance(value, dict):
        return {k: (f"[image:{len(v)} chars]" if k == "data_uri" and isinstance(v, str)
                    else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_case(case, repeat, run_id, ws_url, collector, secret, manifest):
    import websockets
    user = f"{run_id}-{case['id'].lower()}-r{repeat}"
    session = user + "-session-1"
    lane = case.get("lane") or "read_only"
    write = lane in WRITE_LANES
    allowed_keys = set(case.get("vehicle_keys") or []) if lane == "simulated_vehicle" else set()
    case_failures: list[str] = []
    scopes = list(SCOPES) + [x for x in case.get("scopes_extra") or [] if x in SCOPE_EXTRAS]
    token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1",
                          scopes=scopes, timeout_s=1800)
    pending = ""
    rows = []
    baseline = await audit._settled_vehicle_state(collector, include_unmanaged=True)
    if not baseline.settled:
        raise RuntimeError("vehicle baseline did not settle")
    async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8*1024*1024) as ws:
        try:
            await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
        except asyncio.TimeoutError:
            pass
        turns = list(case["turns"]) + [{"say": "取消", "cancel_pending": True, "cleanup": True}]
        for n, turn in enumerate(turns, 1):
            if turn.get("cancel_pending") and not pending:
                rows.append({"turn": n, "say": turn["say"], "skipped": "no_pending",
                             "cleanup": bool(turn.get("cleanup"))})
                continue
            trace = uuid.uuid4().hex
            start = time.monotonic()
            meta = {"llm_provider": manifest["chat"]["provider"], "llm_model": manifest["chat"]["model"]}
            if case.get("location"):            # 7 月旅程的 setup.location：这次会话的当前位置
                meta.update(current_lat=case["location"]["lat"], current_lng=case["location"]["lng"])
            meta.update(case.get("meta") or {})  # 条目级 meta（校验过：只有语音来源）
            # 写车道声明的确认轮与客户端确认按钮同形：点名那条挂起、带 is_confirmation（§12.3）
            obs = await wire._one_turn(ws, session, turn["say"], trace_id=trace,
                                       operation_id=pending if (turn.get("cancel_pending") or turn.get("confirm")) else "",
                                       is_confirmation=bool(turn.get("confirm")),
                                       meta_overrides=meta)
            elapsed = (time.monotonic()-start)*1000
            detail = await audit._fetch_detail(collector, trace)
            calls = detail.get("llm_calls") or []
            evidence_errors = []
            if (detail.get("turn") or {}).get("trace_id") != trace or detail.get("error"):
                evidence_errors.append("trace_incomplete")
            for call in calls:
                if (call.get("provider"), call.get("model")) != (
                        manifest["chat"]["provider"], manifest["chat"]["model"]):
                    evidence_errors.append("model_drift")
            if obs.get("operation_id"):
                pending = obs["operation_id"]
            if pending in (obs.get("closed_operation_ids") or []):
                pending = ""
            # 只读车道等车态回到快照才算稳定；写车道车态本来就会变，只等它稳定
            after = await audit._settled_vehicle_state(collector, required_keys=set(baseline.value),
                                                       expected=None if write else baseline.value,
                                                       include_unmanaged=True)
            verdict = judge(turn.get("expect") or {}, obs, detail, query=turn["say"], elapsed_s=elapsed / 1000,
                            lane=lane, vehicle_after=after.value)
            if turn.get("cancel_pending") and pending:
                verdict["failures"].append("pending_not_closed")
            changed = {k for k in baseline.value.keys() | after.value.keys()
                       if baseline.value.get(k) != after.value.get(k)}
            if not after.settled:
                evidence_errors.append("vehicle_not_settled")
            undeclared = sorted(changed - allowed_keys)
            if write and undeclared:
                verdict["failures"].append("vehicle_changed_undeclared:" + ",".join(undeclared))
            elif changed and not write:
                verdict["failures"].append("vehicle_changed")
            # Store only this synthetic request's trace, never signed URL/token or account config.
            obs = _redact(obs)
            obs["card_text"] = json.dumps(_redact(json.loads(obs.get("card_text") or "{}")), ensure_ascii=False)
            rows.append({"turn": n, "say": turn["say"], "trace_id": trace,
                         "cleanup": bool(turn.get("cleanup")), "observation": obs,
                         "request_to_final_ms": round(elapsed, 2), "verdict": verdict,
                         "evidence_errors": evidence_errors, "vehicle_diff_keys": sorted(changed),
                         "vehicle_before": baseline.value, "vehicle_after": after.value,
                         "trace": _redact(detail)})
            print(f"{case['id']} r{repeat} t{n}: {verdict['failures']} {evidence_errors}", flush=True)
            if write:
                if evidence_errors or (changed - allowed_keys):
                    break                      # 停止取样，但写车道照样走收尾与核对（车态与数据必须复位）
                continue
            if obs.get("actions") or changed or evidence_errors:
                # Stop all business sampling. Only cancel a known pending operation;
                # never confirm it or issue inverse vehicle commands to hide a difference.
                if pending:
                    cleanup = await wire._one_turn(
                        ws, session, "取消", operation_id=pending,
                        meta_overrides={"llm_provider": manifest["chat"]["provider"],
                                        "llm_model": manifest["chat"]["model"]})
                    rows[-1]["stop_cleanup"] = cleanup
                    if pending in (cleanup.get("closed_operation_ids") or []):
                        pending = ""
                return {"id": case["id"], "family": case["family"], "repeat": repeat,
                        "session": session, "rows": rows, "open_operation": bool(pending), "stop": True}
        if write:
            pending = await _write_lane_cleanup(ws, session, case, lane, pending, manifest, rows)
    if write:
        case_failures.extend(await _verify_write_lane(case, lane, user, run_id, collector, baseline.value, rows))
    stop = bool(pending) or (write and (bool(case_failures) or any(r.get("evidence_errors") for r in rows)))
    return {"id": case["id"], "family": case["family"], "repeat": repeat, "lane": lane,
            "session": session, "rows": rows, "open_operation": bool(pending), "stop": stop,
            "case_failures": case_failures, "scopes_extra": scopes[len(SCOPES):]}


async def _write_lane_cleanup(ws, session, case, lane, pending, manifest, rows) -> str:
    """R2 收尾话轮（旅程声明，经规划与 VAL 执行；声明了要确认的按挂起的 operation_id 确认），最后取消残留挂起。"""
    meta = {"llm_provider": manifest["chat"]["provider"], "llm_model": manifest["chat"]["model"]}
    if pending:
        out = await wire._one_turn(ws, session, "取消", operation_id=pending, meta_overrides=meta)
        rows.append({"turn": "cleanup", "say": "取消", "cleanup": True, "observation": _redact(out)})
        if pending in (out.get("closed_operation_ids") or []):
            pending = ""
    for turn in (case.get("cleanup") or []) if lane == "simulated_vehicle" else []:
        out = await wire._one_turn(ws, session, turn["say"], meta_overrides=meta)
        rows.append({"turn": "cleanup", "say": turn["say"], "cleanup": True, "observation": _redact(out)})
        if turn.get("confirm") and out.get("need_confirm") and out.get("operation_id"):
            done = await wire._one_turn(ws, session, "确认", operation_id=out["operation_id"],
                                        is_confirmation=True, meta_overrides=meta)
            rows.append({"turn": "cleanup", "say": "确认", "cleanup": True, "observation": _redact(done)})
        elif out.get("need_confirm") and out.get("operation_id"):
            pending = out["operation_id"]     # 收尾话轮意外挂起：不确认，交给下面的取消
    if pending:
        out = await wire._one_turn(ws, session, "取消", operation_id=pending, meta_overrides=meta)
        rows.append({"turn": "cleanup", "say": "取消", "cleanup": True, "observation": _redact(out)})
        if pending in (out.get("closed_operation_ids") or []):
            pending = ""
    return pending


async def _verify_write_lane(case, lane, user, run_id, collector, baseline: dict, rows) -> list[str]:
    """收尾核对（§12）：R2 车态与跑前快照逐键一致；R1 每类合成数据删后回读为零。任何一项不成立都判失败。"""
    failures = []
    if lane == "simulated_vehicle":
        final = await audit._settled_vehicle_state(collector, attempts=24, required_keys=set(baseline),
                                                   expected=baseline, include_unmanaged=True)
        diff = vehicle_restore_diff(baseline, final.value)
        if diff:
            failures.append("vehicle_not_restored:" + ",".join(diff))
        rows.append({"turn": "verify", "cleanup": True, "vehicle_after": final.value, "vehicle_diff_keys": diff})
    if lane == "synthetic_writes":
        stores = list(case.get("data_cleanup") or [])
        result = await asyncio.to_thread(cleanup_synthetic_data, user, run_id, stores)
        leftovers = data_leftovers(result, stores)
        if leftovers:
            failures.append("data_not_cleaned:" + json.dumps(leftovers, ensure_ascii=False, sort_keys=True))
        rows.append({"turn": "verify", "cleanup": True, "data_cleanup": result})
    return failures


def journey_summary(runs: list[dict], repeat: int) -> dict:
    """按旅程汇总（设计 §2）：一次运行全部轮次无业务失败、无证据错误、没被叫停才算过；达标 = 每一次都过。"""
    journeys: dict = {}
    for c in runs:
        ok = not c["stop"] and not c.get("case_failures") and all(
            not r["verdict"]["failures"] and not r["evidence_errors"]
            for r in c["rows"] if not r.get("skipped") and "verdict" in r)
        j = journeys.setdefault(c["id"], {"family": c["family"], "runs": 0, "passes": 0})
        j["runs"] += 1
        j["passes"] += int(ok)
    return {"journeys": journeys,
            "journeys_all_pass": sum(1 for j in journeys.values() if j["runs"] == repeat and j["passes"] == repeat)}


def _inputs_unchanged(runner_sha: str) -> bool:
    """跑数期间输入没变：跑数输入在开跑提交与 HEAD 之间没有差异、也没有未提交改动。共享 main 上别的会话提交别处不算变化。"""
    if _git("status", "--porcelain", "--", *_FREEZE_PATHSPEC):
        return False
    return not _git("diff", "--name-only", runner_sha, "HEAD", "--", *_FREEZE_PATHSPEC)


def _skip_reason(case: dict, lanes: set, skip_providers: set) -> str:
    """这条旅程这次为什么不跑（空 = 跑）：车道没开、用到 v2 运行器不支持的原语、依赖的外部服务被点名跳过。都记「未测」。"""
    if case.get("lane", "read_only") not in lanes:
        return "lane:" + case.get("lane", "")
    if case.get("unsupported"):
        return "unsupported:" + ",".join(case["unsupported"])
    blocked = sorted(set(case.get("providers") or []) & skip_providers)
    return ("provider:" + ",".join(blocked)) if blocked else ""


async def run(args):
    manifest_path = getattr(args, "manifest", None)
    corpus = Path(manifest_path or getattr(args, "corpus", CORPUS)).resolve()
    loader = load_manifest if manifest_path else load_cases
    cases = loader(corpus)
    lanes = set(getattr(args, "lane", None) or ["read_only"])
    # 写车道要显式点名（--lane），授权依据见 LANE_AUTHORIZATION（冻结设计 §9：用户 2026-10-09 长期授权）
    skip_providers = {x for x in str(getattr(args, "skip_providers", "") or "").split(",") if x}
    if not manifest_path and corpus != CORPUS.resolve():
        from scripts.probe_manual_rag_full_coverage import validate_live_query_safety
        validate_live_query_safety([
            {"id": case["id"], "query": turn["say"]}
            for case in cases for turn in case["turns"] if not turn.get("cancel_pending")
        ])
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
        if {c["id"] for c in cases} != wanted:
            raise ValueError("unknown case ID")
    skipped = [{"id": c["id"], "family": c["family"], "reason": r}
               for c in cases for r in [_skip_reason(c, lanes, skip_providers)] if r]
    cases = [c for c in cases if not _skip_reason(c, lanes, skip_providers)]
    manifest = freeze(args.expected_sha, args.provider, args.model, corpus, loader=loader)
    ws, collector, secret = identity._endpoints()
    payload = {"manifest": manifest, "selected_ids": [c["id"] for c in cases], "skipped": skipped,
               "repeat": args.repeat, "runs": [], "release_start": audit.cloud_release_snapshot(args.expected_sha)}
    if lanes & set(WRITE_LANES):
        # 授权记录进报告（§12.3）：依据、范围、这次点名的写车道与时间
        payload["lane_authorization"] = {**LANE_AUTHORIZATION, "lanes": sorted(lanes & set(WRITE_LANES)),
                                         "at": datetime.now(timezone.utc).isoformat()}
    out = Path(args.out)
    if out.exists():
        raise ValueError("output already exists; use a new run artifact")
    if payload["release_start"]["failures"]:
        _write(out, payload)
        return 2
    run_id = "e2e-v2-" + uuid.uuid4().hex[:12]
    try:
        for repeat in range(1, args.repeat+1):
            for case in cases:
                result = await run_case(case, repeat, run_id, ws, collector, secret, manifest)
                payload["runs"].append(result)
                _write(out, payload)
                if result["stop"]:
                    return 2
    except Exception as exc:
        # Transport exception strings may contain the signed WS URL. Never emit them.
        payload["probe_error"] = type(exc).__name__
        print("probe stopped: " + type(exc).__name__, flush=True)
        return 2
    finally:
        payload["release_end"] = audit.cloud_release_snapshot(args.expected_sha)
        payload["continuity_errors"] = audit.validate_release_continuity(
            payload["release_start"], payload["release_end"], args.expected_sha)
        payload["runner_unchanged"] = _inputs_unchanged(manifest["runner_sha"])
        # 收尾与核对行（写车道）不带逐轮判定，只统计被测轮
        rows = [r for c in payload["runs"] for r in c["rows"] if not r.get("skipped") and "verdict" in r]
        payload["summary"] = {
            "complete_cases": len(payload["runs"]), "expected_cases": len(cases)*args.repeat,
            "measured_turns": len(rows),
            "business_failures": sum(bool(r["verdict"]["failures"]) for r in rows),
            "evidence_failures": sum(bool(r["evidence_errors"]) for r in rows),
            "open_operations": sum(c["open_operation"] for c in payload["runs"]),
            "cleanup_failures": sum(bool(c.get("case_failures")) for c in payload["runs"]),
            "skipped_journeys": len(payload.get("skipped") or []),
        }
        payload["summary"].update(journey_summary(payload["runs"], args.repeat))
        _write(out, payload)
    return 2 if payload["continuity_errors"] or not payload["runner_unchanged"] else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--expected-sha", default="")
    p.add_argument("--provider", default="minimax")
    p.add_argument("--model", default="MiniMax-M3")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--ids", default="")
    p.add_argument("--corpus", type=Path, default=CORPUS,
                   help="committed regression corpus; custom corpora must pass the read-only question preflight")
    p.add_argument("--manifest", type=Path, default=None,
                   help="core journey manifest (docs/design/2026-10-09-v2-core-journey-freeze.md); overrides --corpus")
    p.add_argument("--lane", action="append", choices=LANES,
                   help="lanes to run (default read_only; write lanes need per-batch authorization)")
    p.add_argument("--skip-providers", default="",
                   help="comma-separated provider keys whose journeys are recorded as not measured (e.g. AMAP_KEY)")
    p.add_argument("--out", default=".artifacts/v2-runtime/baseline.json")
    args = p.parse_args()
    if args.repeat < 1 or args.repeat > 5:
        p.error("repeat must be 1..5")
    if args.dry_run:
        cases = load_manifest(args.manifest) if args.manifest else load_cases(args.corpus)
        print(json.dumps({"cases": cases, "scopes": SCOPES}, ensure_ascii=False, indent=2))
        return 0
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        print("baseline preflight failed: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
