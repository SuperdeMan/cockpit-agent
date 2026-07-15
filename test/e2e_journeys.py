"""旅程级端到端 runner：跨 Agent 自主执行 × 全场景连续对话 × 二次交互（协议层）。

设计：docs/design/2026-07-14-journey-e2e-test-system.md
语料：test/journeys/*.yaml
  - level: regression（必须绿，红=回归）| target（能力标尺，允许红——红灯=工程 backlog）
  - lane:  mock（MockProvider 下确定性可跑，nightly 用）| live（需真 LLM/真 provider）
  - requires: 缺 key 自动 SKIP（key 从根 .env 读**是否存在**，绝不打印值；`A|B` 表示任一即可）

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
报告：docs/reviews/eval/journeys_report.{json,md}（--no-report 关）；
失败轮自动 POST collector badcase（--no-badcase 关），dashboard 收藏夹可直接重放下钻。
退出码：回归级有失败 =1；目标级失败不改变退出码（--strict-target 时也算失败）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.request
import uuid
from pathlib import Path

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

ROOT = Path(__file__).resolve().parent.parent
JOURNEY_DIR = ROOT / "test" / "journeys"
REPORT_DIR = ROOT / "docs" / "reviews" / "eval"

URL = "ws://localhost:8090/ws"
COLLECTOR = "http://localhost:8092"
LLM_HTTP = "http://localhost:50059"

# 全局诚实红线（设计 §5.4 G）：泄漏与断链话术。旅程可 no_default_not: true 退出。
DEFAULT_SPEECH_NOT = ["<think>", "```", '{"answer"', "**",
                      "麻烦您再说一遍", "没有待确认的操作"]

# schema 严格键校验：拼错的断言键静默不生效比断言失败更危险。
JOURNEY_KEYS = {"id", "title", "level", "lane", "tags", "requires", "retry",
                "setup", "turns", "cleanup", "final_vehicle", "no_default_not",
                "session_prefix", "notes"}
TURN_KEYS = {"say", "press", "confirm", "cancel", "wait_push", "env", "sleep",
             "expect", "skip_journey_if_speech_any", "new_session", "name"}
EXPECT_KEYS = {"speech_any", "speech_all", "speech_not", "cards_any",
               "card_contains", "need_confirm", "follow_up_any", "action",
               "action_absent", "no_duplicate_action", "process_min",
               "latency_s", "vehicle", "any_of"}
PRESS_KEYS = {"button", "text", "from"}
WAIT_PUSH_KEYS = {"timeout_s", "speech_any", "card_any", "source"}
SETUP_KEYS = {"vehicle", "say", "location", "docker_stop"}


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
    """根 .env 里**值非空**的 key 集合（只看存在性，绝不读值出去）。"""
    keys: set[str] = set()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if v.strip():
                    keys.add(k.strip())
    return keys


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
        self._ws = await websockets.connect(URL)
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
                   is_confirmation: bool, recv_timeout: float) -> TurnOutcome:
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
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({
            "text": text, "session_id": session,
            "is_confirmation": is_confirmation, "meta": meta,
        }))
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
    subprocess.run(["docker", "compose", "-f", str(ROOT / "compose.yaml"), verb, service],
                   check=True, capture_output=True, timeout=180)


async def _run_once(j: dict, listener: PushListener, enforce_latency: bool,
                    do_badcase: bool, res: JourneyResult) -> bool:
    prefix = j.get("session_prefix") or "e2e-jrn-"      # memtest- 用于记忆抽取旅程（§9.2）
    sess = {"id": f"{prefix}{j['id'].lower()}-{int(time.time())}"}
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
                await run_turn(str(text), sess["id"], build_meta(), False, 60)
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
            await run_turn(text, sess["id"], build_meta(), False, 60)
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
                sess["id"] = f"{sess['id'].rsplit('-', 1)[0]}-{int(time.time())}"
                rec["new_session"] = True
            if "say" in turn:
                text, is_conf, meta = str(turn["say"]), False, build_meta()
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
            out = await run_turn(text, sess["id"], meta,
                                 is_conf, recv_timeout=max(budget + 30, 60))
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
    files = sorted(JOURNEY_DIR.glob("*.yaml"))
    if suite_filter:
        files = [f for f in files if suite_filter in f.stem]
    out: list[dict] = []
    seen: set[str] = set()
    for f in files:
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for j in doc.get("journeys") or []:
            errs = validate_journey(j)
            if errs:
                raise SystemExit(f"[schema] {f.name} {j.get('id', '?')}: {errs}")
            if j["id"] in seen:
                raise SystemExit(f"[schema] 旅程 id 重复: {j['id']}")
            seen.add(j["id"])
            j["_file"] = f.name
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
                 lane: str, started: float) -> tuple[dict, str]:
    def bucket(pred):
        rs = [r for r in results if pred(r)]
        return sum(1 for r in rs if r.status == "pass"), \
            sum(1 for r in rs if r.status != "skip")

    tags_all = ["autonomy", "continuity", "honesty", "proactive", "interaction", "safety"]
    scorecard = {}
    for tag in tags_all:
        p, n = bucket(lambda r, t=tag: t in (r.j.get("tags") or []))
        if n:
            scorecard[tag] = f"{p}/{n}"
    lat = [t["elapsed"] for r in results for t in r.turns if "elapsed" in t]
    latency = {"p50": round(statistics.median(lat), 1) if lat else None,
               "p95": round(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)], 1) if lat else None,
               "max": round(max(lat), 1) if lat else None, "n_turns": len(lat)}

    reg_p, reg_n = bucket(lambda r: r.j["level"] == "regression")
    tgt_p, tgt_n = bucket(lambda r: r.j["level"] == "target")
    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_s": round(time.time() - started, 1),
        "provider": provider, "lane": lane or "all",
        "regression": {"pass": reg_p, "total": reg_n},
        "target": {"pass": tgt_p, "total": tgt_n},
        "skipped": [{"id": r.id, "reason": r.reason} for r in results if r.status == "skip"],
        "scorecard": scorecard, "latency_s": latency,
        "journeys": [{
            "id": r.id, "title": r.j["title"], "level": r.j["level"],
            "lane": r.j["lane"], "status": r.status, "reason": r.reason,
            "attempts": r.attempts, "turns": r.turns,
        } for r in results],
    }

    lines = [
        "# 旅程级 e2e 报告（journeys_report）", "",
        f"- 生成时间：{data['generated_at']}（耗时 {data['duration_s']}s）",
        f"- active LLM：`{provider}`（跨 provider 结果不可直接对比）",
        f"- 车道：{data['lane']}",
        f"- **回归级 {reg_p}/{reg_n}**（必须全绿）；目标级 {tgt_p}/{tgt_n}（红灯=工程 backlog）",
        f"- 时延（全轮）：P50={latency['p50']}s P95={latency['p95']}s max={latency['max']}s n={latency['n_turns']}",
        "", "## 记分卡", "",
        "| 维度 | 通过 |", "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in scorecard.items()]
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


# ───────────────────────── main ─────────────────────────

async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", default="", help="按语料文件名过滤（子串）")
    ap.add_argument("--id", default="", help="逗号分隔旅程 id")
    ap.add_argument("--lane", default="", choices=["", "mock", "live"],
                    help="mock=仅确定性子集")
    ap.add_argument("--level", default="", choices=["", "regression", "target"])
    ap.add_argument("--list", action="store_true", help="只列语料不执行")
    ap.add_argument("--enforce-latency", action="store_true",
                    help="时延超预算判失败（默认只记基线）")
    ap.add_argument("--strict-target", action="store_true",
                    help="目标级失败也让退出码=1")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--force-report", action="store_true",
                    help="局部跑（--id/--suite/--lane/--level）也覆盖 canonical 报告")
    ap.add_argument("--no-badcase", action="store_true")
    args = ap.parse_args()

    ids = {x.strip() for x in args.id.split(",") if x.strip()}
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

    provider = active_provider()
    print(f"=== 旅程级 e2e：{len(journeys)} 条 | active LLM: {provider} ===\n")
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

    data, md = build_report(results, provider, args.lane, started)
    # 报告工件纪律：journeys_report.{json,md} 是 canonical 全量基线（入库），只有
    # **无过滤的全量跑**才默认覆盖——否则一次 --id 局部验证就把 33 条收官报告顶掉
    # （批次1 踩过）。局部跑要留档用 --force-report。
    is_full_run = not (ids or args.suite or args.lane or args.level)
    if not args.no_report and (is_full_run or args.force_report):
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "journeys_report.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (REPORT_DIR / "journeys_report.md").write_text(md, encoding="utf-8")
        print(f"报告已写 {REPORT_DIR / 'journeys_report.md'}")
    elif not args.no_report:
        print("（局部跑不覆盖 canonical 报告；要留档加 --force-report）")

    reg_fail = [r for r in results if r.status == "fail" and r.j["level"] == "regression"]
    tgt_fail = [r for r in results if r.status == "fail" and r.j["level"] == "target"]
    print(f"=== 回归级 {data['regression']['pass']}/{data['regression']['total']}"
          f" | 目标级 {data['target']['pass']}/{data['target']['total']}"
          f" | skip {len(data['skipped'])} ===")
    if reg_fail:
        print("回归级失败: " + ", ".join(r.id for r in reg_fail))
    if tgt_fail:
        print("目标级红灯: " + ", ".join(r.id for r in tgt_fail))
    return 1 if reg_fail or (args.strict_target and tgt_fail) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
