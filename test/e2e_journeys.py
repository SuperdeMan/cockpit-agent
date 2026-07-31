"""旅程级端到端 runner：跨 Agent 自主执行 × 全场景连续对话 × 二次交互（协议层）。

设计：docs/design/2026-07-14-journey-e2e-test-system.md
语料：test/journeys/*.yaml
  - level: regression（必须绿，红=回归）| target（能力标尺，允许红——红灯=工程 backlog）
  - lane:  mock（MockProvider 下确定性可跑，nightly 用）| live（需真 LLM/真 provider）
  - requires: 缺 key 自动 SKIP（仅检查父 runner 传入的进程环境是否存在，绝不读取 .env 或打印值；`A|B` 表示任一即可）

协议事实（runner 的模拟保真度依据，见设计文档 §1.3）：
  - HMI 二次交互 = 合成一句文本发送（Cards.tsx 收口 onAction(text)→send）；仅确认条带
    is_confirmation、POI 详情带 meta.nearby_poi_id —— 所以 press 原语直接取上一轮实收卡片
    buttons[].send_text 原样回发即等价于用户点击。
  - 主动推送：NATS agent.proactive → edge-gateway 广播所有已连 WS 客户端
    {"type":"proactive","speech",...,"source":agent_id,"card"?}（gateway/edge/main.go:362）
    —— runner 常驻一条监听 WS 即可等推送。

前置：make up 起全栈（容器重建后 settle ≥40s）。依赖：pip install websockets pyyaml
用法：
  python test/e2e_journeys.py                       # 全部旅程
  python test/e2e_journeys.py --lane mock           # 仅 mock-safe 子集（nightly）
  python test/e2e_journeys.py --level regression    # 仅回归级
  python test/e2e_journeys.py --id A1-1,B4-1        # 指定旅程
  python test/e2e_journeys.py --list                # 列语料不执行
报告：始终先写 runner 分配的 E2E_ARTIFACT_DIR；canonical 仅由父 runner 原子晋升；
失败轮自动 POST collector badcase（--no-badcase 关），dashboard 收藏夹可直接重放下钻。
退出码：回归级有失败 =1；目标级失败不改变退出码（--strict-target 时也算失败）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (
    str(_ROOT),
    str(_ROOT / "test"),
    str(_ROOT / "gen" / "python"),
):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from scripts.e2e_contract import (
    ManifestError,
    atomic_write_report_pair,
    strict_json_loads,
    strict_yaml_load,
)
from support.e2e import CaseRecorder

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)
try:
    import yaml
except ImportError:
    print("请先：pip install pyyaml")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录兄弟模块（eval_common）
from eval_common import ProviderLock  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STACK_ROOT = Path(os.getenv("E2E_STACK_ROOT") or ROOT).resolve()
JOURNEY_DIR = ROOT / "test" / "journeys"

URL = "ws://localhost:8090/ws"
COLLECTOR = "http://localhost:8092"
LLM_HTTP = "http://localhost:50059"

# 全局诚实红线（设计 §5.4 G）：泄漏与断链话术。旅程可 no_default_not: true 退出。
DEFAULT_SPEECH_NOT = ["<think>", "```", '{"answer"', "**",
                      "麻烦您再说一遍", "没有待确认的操作"]

# schema 严格键校验：拼错的断言键静默不生效比断言失败更危险。
JOURNEY_KEYS = {"id", "title", "level", "lane", "tags", "requires", "retry",
                "setup", "turns", "cleanup", "final_vehicle", "no_default_not",
                "notes"}
TURN_KEYS = {"say", "press", "confirm", "cancel", "wait_push", "env", "sleep",
             "expect", "skip_journey_if_speech_any", "new_session", "name"}
EXPECT_KEYS = {"speech_any", "speech_all", "speech_not", "cards_any",
               "card_contains", "need_confirm", "follow_up_any", "action",
               "action_absent", "no_duplicate_action", "process_min",
               "latency_s", "vehicle", "any_of"}
PRESS_KEYS = {"button", "text", "from"}
WAIT_PUSH_KEYS = {"timeout_s", "speech_any", "card_any", "source"}
SETUP_KEYS = {"vehicle", "say", "location", "docker_stop"}
_RECORDER: CaseRecorder | None = None
# B3-3 owns two plain business sessions bound to runner-issued capabilities.
# General journey sessions start after that reserved range so IDs never collide.
_SESSION_NUMBER = 2
_RUNTIME_SPORTS_CONTEXT: dict[str, str] | None = None
_RUNTIME_SPORTS_TOKENS = {
    "$E2E_SPORTS_SCHEDULE_QUERY",
    "$E2E_SPORTS_SINGLE_REMINDER_QUERY",
}
_SPORTS_LEAGUE_QUERIES = {
    1: "世界杯",
    2: "欧冠",
    3: "欧联",
    39: "英超",
    61: "法甲",
    78: "德甲",
    88: "荷甲",
    135: "意甲",
    140: "西甲",
    169: "中超",
}


def _e2e() -> CaseRecorder:
    if _RECORDER is None:
        raise RuntimeError("CaseRecorder is not initialized")
    return _RECORDER


def _next_session() -> str:
    global _SESSION_NUMBER
    _SESSION_NUMBER += 1
    return _e2e().session_id(_SESSION_NUMBER)


def select_runtime_sports_context(
    fixtures: list,
    *,
    day_offset: int,
) -> dict[str, str] | None:
    """Select a real, future fixture that the product can query structurally."""
    date_word = "今天" if day_offset == 0 else "明天"
    for fixture in fixtures:
        if str(getattr(fixture, "status", "")) != "scheduled":
            continue
        league = _SPORTS_LEAGUE_QUERIES.get(
            int(getattr(fixture, "league_id", 0) or 0),
        )
        if league:
            return {"date_word": date_word, "league": league}
    return None


def render_runtime_say(text: str, context: dict[str, str]) -> str:
    """Render only declared runtime tokens; ordinary journey text is untouched."""
    prefix = f"{context['date_word']}{context['league']}"
    if text == "$E2E_SPORTS_SCHEDULE_QUERY":
        return f"{prefix}有哪些比赛"
    if text == "$E2E_SPORTS_SINGLE_REMINDER_QUERY":
        return f"{prefix}第一场是谁踢？开赛前提醒我"
    return text


async def _discover_runtime_sports_context() -> dict[str, str]:
    """Probe the real provider for today/tomorrow; lack of data is a hard failure."""
    from agents.info.src.providers import build_sports_provider

    provider = build_sports_provider()
    now = datetime.now(timezone(timedelta(hours=8)))
    errors: list[str] = []
    for day_offset in (0, 1):
        date = (now + timedelta(days=day_offset)).strftime("%Y-%m-%d")
        try:
            fixtures = await provider.fixtures(date=date)
        except Exception as exc:
            errors.append(f"{date}: {type(exc).__name__}")
            continue
        context = select_runtime_sports_context(
            fixtures,
            day_offset=day_offset,
        )
        if context:
            return context
    detail = f"；provider errors={errors}" if errors else ""
    raise RuntimeError(
        "真实赛事窗口（今天/明天）没有可提醒的已支持联赛场次"
        + detail,
    )


async def resolve_runtime_say(text: str) -> str:
    global _RUNTIME_SPORTS_CONTEXT
    if text not in _RUNTIME_SPORTS_TOKENS:
        return text
    if _RUNTIME_SPORTS_CONTEXT is None:
        _RUNTIME_SPORTS_CONTEXT = await _discover_runtime_sports_context()
    return render_runtime_say(text, _RUNTIME_SPORTS_CONTEXT)


def _session_for_journey(journey: dict, number: int) -> str:
    """Every WS turn uses a plain runner-namespaced business session."""
    if journey.get("id") == "B3-3":
        return _e2e().session_id(number)
    return _next_session()


def _memory_capability_for_journey(journey: dict, number: int) -> str:
    """Only B3-3 receives a runner-issued synthetic-extraction capability."""
    if journey.get("id") == "B3-3":
        return _e2e().memory_capability(number)
    return ""


# ───────────────────────── 基础设施（复用 e2e_scene 成熟原语） ─────────────────────────

def http_json(url: str, payload: dict | None = None, timeout: int = 10):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def vehicle_state() -> dict:
    return http_json(f"{COLLECTOR}/api/vehicle/state")


def debug_vehicle(key: str, value) -> None:
    http_json(f"{COLLECTOR}/api/debug/vehicle", {"key": key, "value": value})


def load_env_keys() -> set[str]:
    """Return non-empty process-environment keys without inspecting ``.env``."""
    return {
        key
        for key, value in os.environ.items()
        if isinstance(key, str) and isinstance(value, str) and value
    }


def active_provider() -> str:
    """报告声明 active LLM（跨 provider 基线不可比，R4.4 的坑）。"""
    try:
        st = http_json(f"{LLM_HTTP}/api/llm/providers")
        act = st.get("active") if isinstance(st, dict) else None
        if isinstance(act, dict):
            return f"{act.get('provider', '?')}:{act.get('model', '?')}"
        if isinstance(act, str):
            return act
        return json.dumps(st, ensure_ascii=False)[:80]
    except Exception:
        return "unknown(gateway http 不可达)"


def mark_badcase(trace_id: str, note: str) -> None:
    try:
        http_json(f"{COLLECTOR}/api/turns/{trace_id}/badcase",
                  {"badcase": True, "note": note[:200]})
    except Exception:
        pass  # 观测面不可达不影响测试结论


def card_types(card: dict | None) -> list[str]:
    """收集卡类型，含 card_group 嵌套（items/cards 两种键防御）。"""
    if not card:
        return []
    out = [str(card.get("type", ""))]
    for key in ("items", "cards"):
        for sub in card.get(key) or []:
            if isinstance(sub, dict) and sub.get("type"):
                out.append(str(sub["type"]))
    return out


def card_buttons(card: dict | None) -> list[dict]:
    """收集卡内按钮（含 card_group 嵌套），供 press 原语取 send_text。

    按钮键有两种既有形态：`buttons`（通用）与 `actions`（reminder fired 卡，
    scheduler.py:39 `{label, send_text}`）——只认**带 send_text 的 dict**，
    避免把 scene 卡的 actions_preview 之类误当按钮。"""
    if not card:
        return []
    btns: list[dict] = []

    def collect(c: dict) -> None:
        for key in ("buttons", "actions"):
            for b in c.get(key) or []:
                if isinstance(b, dict) and b.get("send_text"):
                    btns.append(b)

    collect(card)
    for key in ("items", "cards"):
        for sub in card.get(key) or []:
            if isinstance(sub, dict):
                collect(sub)
    return btns


class PushListener:
    """常驻 WS 收 proactive 广播。必须在触发轮**之前**在线（推送不重放）。"""

    def __init__(self) -> None:
        self.frames: list[tuple[float, dict]] = []
        self._task: asyncio.Task | None = None
        self._ws = None

    async def start(self) -> None:
        if self._task:
            return
        self._ws = await websockets.connect(_e2e().ws_url())
        ack = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=10))
        _e2e().confirm_identity_ack(ack)
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") == "proactive":
                    self.frames.append((time.time(), msg))
        except Exception:
            pass  # 断线即停：wait() 超时会如实失败

    async def wait(self, timeout_s: float, since: float,
                   speech_any: list[str], card_any: list[str],
                   source: str) -> dict | None:
        deadline = time.time() + timeout_s

        def match(msg: dict) -> bool:
            if source and str(msg.get("source", "")) != source:
                return False
            sp = str(msg.get("speech", ""))
            if speech_any and not any(k in sp for k in speech_any):
                return False
            if card_any and not any(t in card_any for t in card_types(msg.get("card"))):
                return False
            return True

        while time.time() < deadline:
            for ts, msg in self.frames:
                if ts >= since and match(msg):
                    return msg
            await asyncio.sleep(1.0)
        return None

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass


# ───────────────────────── 单轮执行与断言 ─────────────────────────

class TurnOutcome:
    def __init__(self) -> None:
        self.final: dict = {}
        self.actions: list[dict] = []       # 流式 action 帧 ∪ final.actions
        self.process_events: list[dict] = []
        self.elapsed: float = 0.0
        self.trace_id: str = ""
        self.fails: list[str] = []
        self.skipped: str = ""              # 非空=该轮被跳过的原因（如无挂起确认）


async def run_turn(text: str, session: str, meta: dict,
                   is_confirmation: bool, recv_timeout: float,
                   e2e_memory_capability: str = "") -> TurnOutcome:
    """执行一轮并收齐**全部** final。

    协议事实：混合意图（部分本地+部分上云）一次请求会发多个 final——端侧先 final
    本地结果，再发 speech_delta 占位、云段处理完再 final（edge server.py 快路径 A2）。
    网关无“请求结束”标记，故 final 之后再等一个宽限窗：窗内有新帧（speech_delta/
    process/action）说明云段在路上，回到长超时继续收；窗内静默即视为本轮完结。
    多 final 合并口径向 HMI 对齐：speech 拼接、actions 取并集、ui_card 取最后一张
    非空、need_confirm 任一为真即真。
    """
    out = TurnOutcome()
    out.trace_id = meta.get("trace_id", "")
    finals: list[dict] = []
    t0 = time.time()
    grace = 2.0
    async with websockets.connect(_e2e().ws_url()) as ws:
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        _e2e().confirm_identity_ack(ack)
        payload = {
            "text": text, "session_id": session,
            "is_confirmation": is_confirmation, "meta": meta,
        }
        if e2e_memory_capability:
            payload["e2e_memory_capability"] = e2e_memory_capability
        await ws.send(json.dumps(payload))
        expecting_more = False     # final 之后又见增量/过程帧 → 云段在路上
        while True:
            timeout = recv_timeout if (not finals or expecting_more) else grace
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                if finals and not expecting_more:
                    break                     # 宽限窗静默 → 本轮完结
                if finals:
                    break                     # 云段超时：按已收 final 判定（时延如实体现）
                raise
            msg = json.loads(raw)
            t = msg.get("type")
            if t in ("proactive", "vehicle_state"):
                continue                      # 广播帧与本轮无关
            if t == "speech_delta":
                if finals:
                    expecting_more = True
                continue
            if t == "process":
                out.process_events.append(msg)
                if finals:
                    expecting_more = True
                continue
            if t == "action":
                if isinstance(msg.get("action"), dict):
                    out.actions.append(msg["action"])
                if finals:
                    expecting_more = True
                continue
            if t == "final":
                finals.append(msg)
                out.actions.extend(a for a in (msg.get("actions") or [])
                                   if isinstance(a, dict))
                expecting_more = False
                continue
            if t == "error":
                finals.append(msg)
                out.fails.append(f"error 帧: {msg.get('message', '')[:120]}")
                break
    out.elapsed = time.time() - t0
    if finals:
        merged = dict(finals[-1])
        merged["speech"] = " ".join(str(f.get("speech", "") or "") for f in finals).strip()
        merged["need_confirm"] = any(f.get("need_confirm") for f in finals)
        merged["ui_card"] = next((f["ui_card"] for f in reversed(finals)
                                  if f.get("ui_card")), None)
        merged["follow_up"] = next((f["follow_up"] for f in reversed(finals)
                                    if f.get("follow_up")), "")
        out.final = merged
    return out


def check_expect(expect: dict, out: TurnOutcome, enforce_latency: bool,
                 default_not: list[str]) -> list[str]:
    fails: list[str] = []
    speech = str(out.final.get("speech", "") or "")
    ctypes = card_types(out.final.get("ui_card"))
    card_json = json.dumps(out.final.get("ui_card") or {}, ensure_ascii=False)

    def one(exp: dict) -> list[str]:
        f: list[str] = []
        if "speech_any" in exp and not any(str(k) in speech for k in exp["speech_any"]):
            f.append(f"speech_any 未命中 {exp['speech_any']} | speech={speech[:60]}")
        if "speech_all" in exp:
            miss = [k for k in exp["speech_all"] if str(k) not in speech]
            if miss:
                f.append(f"speech_all 缺 {miss} | speech={speech[:60]}")
        for k in exp.get("speech_not", []):
            if str(k) in speech:
                f.append(f"speech_not 命中禁词 {k!r} | speech={speech[:60]}")
        if "cards_any" in exp and not any(t in exp["cards_any"] for t in ctypes):
            f.append(f"cards_any 未命中 {exp['cards_any']} | 实际={ctypes}")
        if "card_contains" in exp:
            miss = [k for k in exp["card_contains"] if str(k) not in card_json]
            if miss:
                f.append(f"card_contains 缺 {miss}")
        if "need_confirm" in exp and bool(out.final.get("need_confirm")) != bool(exp["need_confirm"]):
            f.append(f"need_confirm={out.final.get('need_confirm')} 期望 {exp['need_confirm']}")
        if "follow_up_any" in exp:
            fu = str(out.final.get("follow_up", "") or "")
            if not any(str(k) in fu for k in exp["follow_up_any"]):
                f.append(f"follow_up_any 未命中 {exp['follow_up_any']} | {fu[:40]}")
        if "action" in exp:
            specs = exp["action"] if isinstance(exp["action"], list) else [exp["action"]]
            for spec in specs:
                hit = None
                for a in out.actions:
                    if str(a.get("type", "")) != str(spec.get("type", "")):
                        continue
                    payload = a.get("payload") or {}
                    if any(k not in payload for k in spec.get("payload_has", [])):
                        continue
                    pm = spec.get("payload_match", {})
                    if any(str(pm[k]) not in json.dumps(payload.get(k, ""), ensure_ascii=False)
                           for k in pm):
                        continue
                    hit = a
                    break
                if hit is None:
                    f.append(f"action 未命中 {spec} | 实际类型={[a.get('type') for a in out.actions]}")
        for atype in exp.get("action_absent", []):
            if any(a.get("type") == atype for a in out.actions):
                f.append(f"不该出现的动作 {atype} 出现了")
        for atype in exp.get("no_duplicate_action", []):
            n = sum(1 for a in out.actions if a.get("type") == atype)
            if n > 1:
                f.append(f"动作 {atype} 重复 {n} 次")
        if "process_min" in exp and len(out.process_events) < int(exp["process_min"]):
            f.append(f"过程区事件 {len(out.process_events)} < {exp['process_min']}")
        if "latency_s" in exp and enforce_latency and out.elapsed > float(exp["latency_s"]):
            f.append(f"时延 {out.elapsed:.1f}s 超预算 {exp['latency_s']}s")
        if "vehicle" in exp:
            st = vehicle_state()
            for k, v in exp["vehicle"].items():
                if st.get(k) != v:
                    f.append(f"车况 {k}={st.get(k)!r} 期望 {v!r}")
        if "any_of" in exp:
            subs = [one(s) for s in exp["any_of"]]
            if all(subs):
                f.append("any_of 全部分支未满足: " + " || ".join(
                    ";".join(s)[:80] for s in subs))
        return f

    fails.extend(one(expect))
    for k in default_not:                     # 全局红线独立于用例 expect
        if k in speech:
            fails.append(f"全局禁词命中 {k!r} | speech={speech[:60]}")
    return fails


# ───────────────────────── 旅程执行 ─────────────────────────

class JourneyResult:
    def __init__(self, j: dict) -> None:
        self.j = j
        self.status = "pass"                  # pass / fail / skip
        self.reason = ""
        self.turns: list[dict] = []
        self.attempts = 1

    @property
    def id(self) -> str:
        return self.j["id"]


async def run_journey(j: dict, env_keys: set[str], listener: PushListener,
                      enforce_latency: bool, do_badcase: bool) -> JourneyResult:
    res = JourneyResult(j)

    # requires：A|B 任一存在即可；live 车道隐含需要真 LLM（mock 栈上跑 live 旅程只会假红）
    reqs = list(j.get("requires", []))
    if j.get("lane") == "live" and "LLM_API_KEY" not in reqs:
        reqs.append("LLM_API_KEY")
    for req in reqs:
        if not any(alt in env_keys for alt in str(req).split("|")):
            res.status, res.reason = "skip", f"缺 {req}"
            return res

    attempts = int(j.get("retry", 0)) + 1
    for attempt in range(1, attempts + 1):
        res.attempts = attempt
        res.turns, res.status, res.reason = [], "pass", ""
        ok = await _run_once(j, listener, enforce_latency, do_badcase, res)
        if ok or res.status == "skip":
            return res
    return res


def _docker(verb: str, service: str) -> None:
    """A5-1 类故障注入：docker compose stop/start（根 compose.yaml，运维铁律）。"""
    import subprocess
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(STACK_ROOT / "compose.yaml"),
            verb,
            service,
        ],
        cwd=STACK_ROOT,
        check=True,
        capture_output=True,
        timeout=180,
    )


async def _run_once(j: dict, listener: PushListener, enforce_latency: bool,
                    do_badcase: bool, res: JourneyResult) -> bool:
    sess = {
        "number": 1,
        "id": _session_for_journey(j, 1),
        "memory_capability": _memory_capability_for_journey(j, 1),
    }
    journey_t0 = time.time()          # wait_push 只认本旅程开始后的推送
    default_not = [] if j.get("no_default_not") else DEFAULT_SPEECH_NOT
    loc = (j.get("setup") or {}).get("location")

    def build_meta(extra: dict | None = None) -> dict:
        meta = {"trace_id": uuid.uuid4().hex[:16]}
        if loc:
            meta.update({
                "current_lat": f"{float(loc['lat']):.6f}",
                "current_lng": f"{float(loc['lng']):.6f}",
                "current_accuracy_m": "10",
                "current_location_source": "browser",
            })
        meta.update(extra or {})
        return meta

    setup = j.get("setup") or {}
    stopped_service = ""
    try:
        ok = await _run_body(j, setup, sess, build_meta, listener, journey_t0,
                             default_not, enforce_latency, do_badcase, res,
                             lambda svc: _mark_stopped(svc))
    finally:
        # cleanup + 故障恢复：尽力而为、无论旅程结论如何都执行
        for text in j.get("cleanup") or []:
            try:
                await run_turn(
                    str(text),
                    sess["id"],
                    build_meta(),
                    False,
                    60,
                    sess.get("memory_capability", ""),
                )
            except Exception:
                pass
        if _STOPPED["svc"]:
            try:
                _docker("start", _STOPPED["svc"])
                await asyncio.sleep(20)   # 重注册窗口，别让下一条旅程踩到半残栈
            except Exception as e:
                print(f"   !! 故障注入恢复失败（{_STOPPED['svc']}）: {e} —— 请手工 docker start")
            _STOPPED["svc"] = ""
    return ok


_STOPPED = {"svc": ""}                    # 单并发 runner：当前被故障注入停掉的服务


def _mark_stopped(svc: str) -> None:
    _STOPPED["svc"] = svc


async def _run_body(j: dict, setup: dict, sess: dict, build_meta, listener,
                    journey_t0: float, default_not: list, enforce_latency: bool,
                    do_badcase: bool, res: JourneyResult, mark_stopped) -> bool:
    # setup：压车况 → 故障注入 → 前置话轮（建立已知起点，防 VAL 持久态/幂等跳过蒙对）
    try:
        for k, v in (setup.get("vehicle") or {}).items():
            debug_vehicle(k, v)
        if setup.get("docker_stop"):
            svc = str(setup["docker_stop"])
            mark_stopped(svc)             # 先登记再执行：stop 半途失败也要恢复
            _docker("stop", svc)
        for text in setup.get("say") or []:
            await run_turn(
                text,
                sess["id"],
                build_meta(),
                False,
                60,
                sess.get("memory_capability", ""),
            )
        if setup.get("vehicle") or setup.get("say"):
            await asyncio.sleep(2.0)
    except Exception as e:
        res.status, res.reason = "fail", f"setup 失败: {e}"
        return False

    last_final: dict = {}
    last_push: dict = {}
    journey_failed = False

    for i, turn in enumerate(j.get("turns") or [], 1):
        expect = turn.get("expect") or {}
        rec: dict = {"i": i}
        try:
            if "sleep" in turn:
                await asyncio.sleep(float(turn["sleep"]))
                rec["op"] = f"sleep {turn['sleep']}"
                res.turns.append(rec)
                continue
            if "env" in turn:
                for k, v in turn["env"].items():
                    debug_vehicle(k, v)
                rec["op"] = f"env {turn['env']}"
                res.turns.append(rec)
                continue
            if "wait_push" in turn:
                spec = turn["wait_push"] or {}
                since = journey_t0           # 本旅程期间的推送都算（含早于本原语到达的）
                msg = await listener.wait(
                    float(spec.get("timeout_s", 120)), since,
                    [str(x) for x in spec.get("speech_any", [])],
                    [str(x) for x in spec.get("card_any", [])],
                    str(spec.get("source", "")))
                rec["op"] = f"wait_push {spec.get('source', '')}"
                if msg is None:
                    rec["fails"] = [f"等推送超时 {spec}"]
                    journey_failed = True
                    res.turns.append(rec)
                    break
                last_push = msg
                rec["push_speech"] = str(msg.get("speech", ""))[:60]
                res.turns.append(rec)
                continue

            # 文本类：say / press / confirm / cancel
            if turn.get("new_session"):       # 跨会话旅程（如记忆抽取→新会话召回）
                sess["number"] += 1
                sess["id"] = _session_for_journey(j, sess["number"])
                sess["memory_capability"] = _memory_capability_for_journey(
                    j,
                    sess["number"],
                )
                rec["new_session"] = True
            if "say" in turn:
                text = await resolve_runtime_say(str(turn["say"]))
                is_conf, meta = False, build_meta()
            elif "press" in turn:
                spec = turn["press"] or {}
                pool = card_buttons(last_push.get("card") if spec.get("from") == "push"
                                    else last_final.get("ui_card"))
                needle = str(spec.get("button", ""))
                btn = next((b for b in pool
                            if needle and (needle in str(b.get("send_text", ""))
                                           or needle in str(b.get("label", "")))), None)
                if btn is None and not spec.get("text"):
                    rec["op"], rec["fails"] = f"press {needle}", \
                        [f"按钮 {needle!r} 不存在 | 可选={[b.get('label') or b.get('send_text') for b in pool]}"]
                    journey_failed = True
                    res.turns.append(rec)
                    break
                text = str(btn.get("send_text") or btn.get("label")) if btn else str(spec["text"])
                is_conf, meta = False, build_meta()
            elif "confirm" in turn:
                if not last_final.get("need_confirm"):
                    rec["op"], rec["skipped"] = "confirm", "无挂起确认（幂等跳过）"
                    res.turns.append(rec)
                    continue
                text, is_conf, meta = "确认", True, build_meta()
            elif "cancel" in turn:
                text, is_conf, meta = "取消", True, build_meta()
            else:
                rec["fails"] = [f"未知 turn 操作: {list(turn.keys())}"]
                journey_failed = True
                res.turns.append(rec)
                break

            budget = float(expect.get("latency_s", 90))
            out = await run_turn(
                text,
                sess["id"],
                meta,
                is_conf,
                recv_timeout=max(budget + 30, 60),
                e2e_memory_capability=sess.get("memory_capability", ""),
            )
            last_final = out.final or last_final
            rec.update({"op": text[:50], "elapsed": round(out.elapsed, 1),
                        "trace_id": out.trace_id,
                        "speech": str(out.final.get("speech", ""))[:80]})

            skip_kws = turn.get("skip_journey_if_speech_any") or []
            speech = str(out.final.get("speech", "") or "")
            if skip_kws and any(k in speech for k in skip_kws):
                res.status, res.reason = "skip", f"数据不可得（第{i}轮命中 {skip_kws}）"
                res.turns.append(rec)
                return True

            if "vehicle" in expect:      # 车况断言前等 VAL 落地 + NATS diff 回镜像
                await asyncio.sleep(2.5)
            fails = out.fails + check_expect(expect, out, enforce_latency, default_not)
            if fails:
                rec["fails"] = fails
                journey_failed = True
                if do_badcase and out.trace_id:
                    mark_badcase(out.trace_id, f"journey {j['id']} 第{i}轮: {fails[0]}")
                res.turns.append(rec)
                break
            res.turns.append(rec)
        except asyncio.TimeoutError:
            rec["fails"] = ["收帧超时（final 未到）"]
            journey_failed = True
            res.turns.append(rec)
            break
        except Exception as e:
            rec["fails"] = [f"执行异常: {e}"]
            journey_failed = True
            res.turns.append(rec)
            break

    # 终态车况（先 settle 再断）
    if not journey_failed and j.get("final_vehicle"):
        await asyncio.sleep(2.5)
        st = vehicle_state()
        bad = [f"{k}={st.get(k)!r} 期望 {v!r}"
               for k, v in j["final_vehicle"].items() if st.get(k) != v]
        if bad:
            journey_failed = True
            res.turns.append({"i": "final", "fails": [f"终态车况: {'; '.join(bad)}"]})

    # cleanup 与故障恢复统一在 _run_once 的 finally 里做（含 skip/异常早退路径）
    if journey_failed:
        res.status = "fail"
        first = next((t for t in res.turns if t.get("fails")), {})
        res.reason = (first.get("fails") or ["?"])[0][:160]
        return False
    return True


# ───────────────────────── 语料装载与校验 ─────────────────────────

def load_journeys(suite_filter: str, id_filter: set[str],
                  lane: str, level: str) -> list[dict]:
    files = sorted({
        *JOURNEY_DIR.rglob("*.yaml"),
        *JOURNEY_DIR.rglob("*.yml"),
    })
    if suite_filter:
        files = [
            f for f in files
            if suite_filter in f.relative_to(JOURNEY_DIR).as_posix()
        ]
    out: list[dict] = []
    seen: set[str] = set()
    for f in files:
        relative = f.relative_to(JOURNEY_DIR).as_posix()
        try:
            doc = strict_yaml_load(
                f.read_text(encoding="utf-8"),
                where=relative,
            ) or {}
        except (OSError, UnicodeError, ManifestError) as exc:
            raise SystemExit(f"[schema] {relative}: {exc}") from exc
        for j in doc.get("journeys") or []:
            errs = validate_journey(j)
            if errs:
                raise SystemExit(f"[schema] {f.name} {j.get('id', '?')}: {errs}")
            if j["id"] in seen:
                raise SystemExit(f"[schema] 旅程 id 重复: {j['id']}")
            seen.add(j["id"])
            j["_file"] = relative
            out.append(j)
    if id_filter:
        out = [j for j in out if j["id"] in id_filter]
    if lane == "mock":
        out = [j for j in out if j.get("lane") == "mock"]
    if level:
        out = [j for j in out if j.get("level") == level]
    return out


def validate_journey(j: dict) -> list[str]:
    errs = [f"未知键 {k}" for k in j if k not in JOURNEY_KEYS]
    for req in ("id", "title", "level", "lane", "turns"):
        if req not in j:
            errs.append(f"缺必填 {req}")
    if j.get("level") not in ("regression", "target"):
        errs.append(f"level 非法: {j.get('level')}")
    if j.get("lane") not in ("mock", "live"):
        errs.append(f"lane 非法: {j.get('lane')}")
    errs += [f"setup 未知键 {k}" for k in (j.get("setup") or {}) if k not in SETUP_KEYS]
    for i, t in enumerate(j.get("turns") or [], 1):
        errs += [f"turn{i} 未知键 {k}" for k in t if k not in TURN_KEYS]
        for k in (t.get("expect") or {}):
            if k not in EXPECT_KEYS:
                errs.append(f"turn{i}.expect 未知键 {k}")
        for sub in (t.get("expect") or {}).get("any_of") or []:
            for k in sub:
                if k not in EXPECT_KEYS:
                    errs.append(f"turn{i}.any_of 未知键 {k}")
        if "press" in t:
            errs += [f"turn{i}.press 未知键 {k}" for k in (t["press"] or {})
                     if k not in PRESS_KEYS]
        if "wait_push" in t:
            errs += [f"turn{i}.wait_push 未知键 {k}" for k in (t["wait_push"] or {})
                     if k not in WAIT_PUSH_KEYS]
    return errs


# ───────────────────────── 报告 ─────────────────────────

def build_report(results: list[JourneyResult], provider: str,
                 lane: str, started: float,
                 lock_summary: dict | None = None,
                 *, metadata: dict | None = None) -> tuple[dict, str]:
    def summarize(rs: list[JourneyResult]) -> dict:
        counts = {
            "selected": len(rs),
            "executed": sum(1 for r in rs if r.status != "skip"),
            "pass": sum(1 for r in rs if r.status == "pass"),
            "fail": sum(1 for r in rs if r.status == "fail"),
            "skip": sum(1 for r in rs if r.status == "skip"),
        }
        counts["summary"] = (
            f"pass/selected={counts['pass']}/{counts['selected']}; "
            f"fail={counts['fail']}; skip={counts['skip']}"
        )
        return counts

    def bucket(pred) -> dict:
        rs = [r for r in results if pred(r)]
        return summarize(rs)

    tags_all = sorted({
        str(tag)
        for result in results
        for tag in (result.j.get("tags") or [])
    })
    scorecard = {}
    for tag in tags_all:
        status = bucket(lambda r, t=tag: t in (r.j.get("tags") or []))
        if status["selected"]:
            scorecard[tag] = status
    lat = [t["elapsed"] for r in results for t in r.turns if "elapsed" in t]
    latency = {"p50": round(statistics.median(lat), 1) if lat else None,
               "p95": round(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)], 1) if lat else None,
               "max": round(max(lat), 1) if lat else None, "n_turns": len(lat)}

    regression = bucket(lambda r: r.j["level"] == "regression")
    target = bucket(lambda r: r.j["level"] == "target")
    lanes = {
        name: bucket(lambda r, value=name: r.j["lane"] == value)
        for name in sorted({str(r.j["lane"]) for r in results})
    }
    suites = {
        name: bucket(
            lambda r, value=name: str(r.j.get("_file") or "unknown") == value
        )
        for name in sorted({str(r.j.get("_file") or "unknown") for r in results})
    }
    overall = summarize(results)
    summary = str(overall["summary"])
    counts = {key: value for key, value in overall.items() if key != "summary"}
    data = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_s": round(time.time() - started, 1),
        "provider": provider, "provider_lock": lock_summary or {},
        "lane": lane or "all",
        "counts": counts,
        "summary": summary,
        "regression": regression,
        "target": target,
        "lanes": lanes,
        "suites": suites,
        "skipped": [{"id": r.id, "reason": r.reason} for r in results if r.status == "skip"],
        "scorecard": scorecard, "latency_s": latency,
        "journeys": [{
            "id": r.id, "title": r.j["title"], "level": r.j["level"],
            "lane": r.j["lane"], "suite": r.j.get("_file", ""),
            "tags": list(r.j.get("tags") or []),
            "status": r.status, "reason": r.reason,
            "attempts": r.attempts, "turns": r.turns,
        } for r in results],
    }
    if metadata:
        data.update(metadata)
    data["counts"] = counts
    data["summary"] = summary

    locked = bool((lock_summary or {}).get("locked"))
    drifts = (lock_summary or {}).get("drifts") or []
    lines = [
        "# 旅程级 e2e 报告（journeys_report）", "",
        f"- 生成时间：{data['generated_at']}（耗时 {data['duration_s']}s）",
        f"- active LLM：`{provider}`"
        + ("（--provider 已锁定）" if locked else "（跨 provider 结果不可直接对比）"),
        f"- run_id：`{data.get('run_id', '(unmanaged)')}`",
        f"- 报告范围：`{data.get('report_scope', 'non_canonical_artifact')}`",
        f"- 摘要：{summary}",
    ]
    if drifts:
        lines.append("- **⚠️ 评测中途 provider 漂移，本报告作废重跑**："
                     + "；".join(f"`{d['from']}`→`{d['to']}`（@{d['at']}）" for d in drifts))
    lines += [
        f"- 车道：{data['lane']}",
        f"- **回归级 {regression['summary']}**（必须全绿）；"
        f"目标级 {target['summary']}（红灯=工程 backlog）",
        f"- 时延（全轮）：P50={latency['p50']}s P95={latency['p95']}s max={latency['max']}s n={latency['n_turns']}",
        "", "## 记分卡", "",
        "| 维度 | 通过 |", "|---|---|",
    ]
    lines += [f"| {k} | {v['summary']} |" for k, v in scorecard.items()]
    lines += [
        "",
        "## 车道与语料分桶",
        "",
        "| 类型 | 名称 | 结果 |",
        "|---|---|---|",
    ]
    lines += [f"| lane | {name} | {value['summary']} |" for name, value in lanes.items()]
    lines += [f"| suite | {name} | {value['summary']} |" for name, value in suites.items()]
    lines += ["", "## 旅程明细", "",
              "| id | 级别 | 结果 | 说明 |", "|---|---|---|---|"]
    icon = {"pass": "✅", "fail": "❌", "skip": "⏭️"}
    for r in results:
        lines.append(f"| {r.id} {r.j['title']} | {r.j['level']} | "
                     f"{icon[r.status]} {r.status} | {r.reason} |")
    reds = [r for r in results if r.status == "fail"]
    if reds:
        lines += ["", "## 红灯清单（每条=一个待决策工作项）", ""]
        for r in reds:
            first = next((t for t in r.turns if t.get("fails")), {})
            lines += [f"### {r.id} {r.j['title']}（{r.j['level']}）",
                      f"- 首损轮：{first.get('i')} `{first.get('op', '')}`",
                      f"- 现象：{'; '.join(first.get('fails', []))[:300]}",
                      f"- trace_id：`{first.get('trace_id', '')}`（dashboard 搜索直达）", ""]
    return data, "\n".join(lines) + "\n"


def build_report_rows(
    rows: list[dict],
    provider: str,
    lane: str,
    duration_s: float,
    lock_summary: dict | None = None,
    *,
    metadata: dict | None = None,
) -> tuple[dict, str]:
    """Rebuild one honest report from sequential, freshly signed shards."""
    results: list[JourneyResult] = []
    seen: set[str] = set()
    for row in rows:
        journey_id = str(row.get("id") or "")
        status = str(row.get("status") or "")
        if not journey_id or journey_id in seen or status not in {
            "pass",
            "fail",
            "skip",
        }:
            raise ValueError("invalid journey report row")
        seen.add(journey_id)
        journey = {
            "id": journey_id,
            "title": str(row.get("title") or ""),
            "level": str(row.get("level") or ""),
            "lane": str(row.get("lane") or ""),
            "_file": str(row.get("suite") or ""),
            "tags": list(row.get("tags") or []),
        }
        result = JourneyResult(journey)
        result.status = status
        result.reason = str(row.get("reason") or "")
        result.attempts = int(row.get("attempts") or 1)
        result.turns = list(row.get("turns") or [])
        results.append(result)
    return build_report(
        results,
        provider,
        lane,
        time.time() - max(0.0, float(duration_s)),
        lock_summary,
        metadata=metadata,
    )


def record_journey_results(
    recorder: CaseRecorder,
    results: list[JourneyResult],
) -> None:
    """Map the actually selected corpus outcomes to strict result counts."""

    for result in results:
        case_id = "journey_" + "".join(
            char.lower() if char.isalnum() else "_"
            for char in result.id
        ).strip("_")
        if result.status == "pass":
            recorder.pass_case(case_id)
        elif result.status == "skip":
            code = (
                "credential_unavailable"
                if result.reason.startswith("缺 ")
                else "data_unavailable"
            )
            recorder.skip_case(case_id, code, result.reason)
        else:
            recorder.fail_case(
                case_id,
                "assertion_failed",
                result.reason,
            )


def _csv_values(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--suite",
        default=os.environ.get("E2E_JOURNEY_SUITES", ""),
        help="按语料文件名过滤（子串）",
    )
    ap.add_argument(
        "--id",
        default=os.environ.get("E2E_JOURNEY_IDS", ""),
        help="逗号分隔旅程 id",
    )
    ap.add_argument(
        "--lane",
        default=os.environ.get("E2E_JOURNEY_LANES", ""),
        choices=["", "mock", "live"],
        help="mock=仅确定性子集",
    )
    ap.add_argument(
        "--level",
        default=os.environ.get("E2E_JOURNEY_LEVELS", ""),
        choices=["", "regression", "target"],
    )
    ap.add_argument("--list", action="store_true", help="只列语料不执行")
    ap.add_argument(
        "--enforce-latency",
        action="store_true",
        help="时延超预算判失败（默认只记基线）",
    )
    ap.add_argument(
        "--strict-target",
        action="store_true",
        help="目标级失败也让退出码=1",
    )
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--no-badcase", action="store_true")
    ap.add_argument(
        "--provider",
        default=os.environ.get("E2E_PROVIDER", ""),
        help="锁定 active LLM 厂商（漂移=报告作废、退出码 1）",
    )
    ap.add_argument(
        "--model",
        default=os.environ.get("E2E_MODEL", ""),
        help="与 provider 一起锁定本次实际模型",
    )
    return ap.parse_args(argv)


def _journey_filters(args: argparse.Namespace) -> dict[str, list[str]]:
    return {
        "ids": _csv_values(args.id),
        "suites": _csv_values(args.suite),
        "lanes": _csv_values(args.lane),
        "levels": _csv_values(args.level),
        "other": [],
    }


def _injected_metadata() -> dict:
    raw = os.environ.get("E2E_CANONICAL_METADATA", "")
    if not raw:
        return {}
    if len(raw.encode("utf-8", errors="ignore")) > 256 * 1024:
        raise RuntimeError("E2E_CANONICAL_METADATA is too large")
    try:
        payload = strict_json_loads(raw)
    except (TypeError, json.JSONDecodeError, ManifestError) as exc:
        raise RuntimeError("E2E_CANONICAL_METADATA is invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("E2E_CANONICAL_METADATA must be an object")
    return payload


def write_report_artifacts(
    artifact_dir: Path,
    data: dict,
    markdown: str,
) -> tuple[Path, Path]:
    """Write the JSON/Markdown pair only inside the runner-owned directory."""

    root = Path(artifact_dir).resolve()
    json_path = root / "journeys_report.json"
    markdown_path = root / "journeys_report.md"
    atomic_write_report_pair(json_path, markdown_path, data, markdown)
    return json_path, markdown_path


# ───────────────────────── main ─────────────────────────

async def _run() -> int:
    args = _parse_args()

    ids = {x.strip() for x in args.id.split(",") if x.strip()}
    declared = load_journeys("", set(), "", "")
    journeys = load_journeys(args.suite, ids, args.lane, args.level)
    if not journeys:
        print("没有匹配的旅程语料")
        return 1
    if args.list:
        for j in journeys:
            print(f"{j['id']:>8}  [{j['level']:>10}/{j['lane']}]  {j['title']}  "
                  f"({j['_file']})")
        print(f"共 {len(journeys)} 条")
        return 0

    lock = ProviderLock(LLM_HTTP, args.provider, args.model)
    try:
        provider = lock.pin()
    except RuntimeError as e:
        print(f"✗ {e}")
        return 2
    if not lock.available:
        provider = active_provider()   # 网关不可达时沿用旧的可读降级串
    print(f"=== 旅程级 e2e：{len(journeys)} 条 | active LLM: {provider}"
          + ("（--provider 已锁定）" if lock.locked else "") + " ===\n")
    env_keys = load_env_keys()
    listener = PushListener()
    if any("wait_push" in t for j in journeys for t in j.get("turns") or []):
        await listener.start()

    started = time.time()
    results: list[JourneyResult] = []
    for j in journeys:
        print(f"── {j['id']} {j['title']} [{j['level']}]")
        r = await run_journey(j, env_keys, listener,
                              args.enforce_latency, not args.no_badcase)
        results.append(r)
        lock.check(j["id"])   # 漂移守卫：每条旅程后复核 active 未被切走/回落
        icon = {"pass": "✅", "fail": "❌", "skip": "⏭️"}[r.status]
        for t in r.turns:
            mark = "✗" if t.get("fails") else ("→" if not t.get("skipped") else "○")
            line = f"   {mark} {t.get('op', '')}"
            if "elapsed" in t:
                line += f"  ({t['elapsed']}s)"
            if t.get("speech"):
                line += f"  {t['speech'][:48]}"
            print(line)
            for fl in t.get("fails", []):
                print(f"     ! {fl[:160]}")
        print(f"   {icon} {r.status.upper()} {r.reason}\n")
    await listener.close()

    filters = _journey_filters(args)
    metadata = _injected_metadata()
    report_scope = (
        "canonical_candidate"
        if metadata
        else "non_canonical_artifact"
    )
    metadata.update({
        "run_id": _e2e().run_id(),
        "model": args.model,
        "report_scope": report_scope,
        "scope": {
            "full": not any(filters.values()) and len(declared) == len(journeys),
            "journey_filters": filters,
            "declared": len(declared),
            "selected": len(journeys),
        },
    })
    data, md = build_report(
        results,
        provider,
        args.lane,
        started,
        lock.summary(),
        metadata=metadata,
    )
    if not args.no_report:
        json_artifact = _e2e().add_artifact(
            "journeys_report.json",
            metadata={"kind": "journeys_report", "format": "json"},
        )
        markdown_artifact = _e2e().add_artifact(
            "journeys_report.md",
            metadata={"kind": "journeys_report", "format": "markdown"},
        )
        write_report_artifacts(json_artifact.parent, data, md)
        print(f"报告工件已写 {markdown_artifact}")

    reg_fail = [r for r in results if r.status == "fail" and r.j["level"] == "regression"]
    tgt_fail = [r for r in results if r.status == "fail" and r.j["level"] == "target"]
    print(f"=== 回归级 {data['regression']['pass']}/{data['regression']['selected']}"
          f" | 目标级 {data['target']['pass']}/{data['target']['selected']}"
          f" | skip {len(data['skipped'])} ===")
    if reg_fail:
        print("回归级失败: " + ", ".join(r.id for r in reg_fail))
    if tgt_fail:
        print("目标级红灯: " + ", ".join(r.id for r in tgt_fail))
    drifted = lock.summary()["drift_detected"]
    if drifted:
        print("⚠️ 评测中途 active provider 漂移（HMI 切换或网关重启回落），报告作废重跑: "
              + "; ".join(f"{d['from']}→{d['to']}@{d['at']}" for d in lock.drifts))
    record_journey_results(_e2e(), results)
    if drifted:
        _e2e().fail_case(
            "provider_lock",
            "provider_drift",
            "active provider changed during the journey run",
        )
    return 1 if reg_fail or drifted or (args.strict_target and tgt_fail) else 0


async def main() -> int:
    global _RECORDER
    recorder = CaseRecorder()
    _RECORDER = recorder
    with recorder:
        await _run()
    return recorder.exit_code()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
