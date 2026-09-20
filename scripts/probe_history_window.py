"""W19-c：规划历史视窗单变量实验（2 / 4 / 6 对），**干净用户**、真栈、collector 判读。

批 5 W19 的首读（同一语料 4 组 × 2、共享 e2e 用户）只是仪器验证；共享用户的长期记忆里躺着探针
留下的同题情景记忆，读数分不清「历史视窗给的」还是「记忆召回给的」。本实验把两条混杂源都抽掉：

  · **干净用户**：走网关的签名 E2E 身份车道（`scripts.e2e_identity.sign_identity`，秘密在根 `.env`
    的 `E2E_IDENTITY_SECRET`，云端 `E2E_IDENTITY_ENABLED=true`）。每个视窗臂一个全新 user
    （`e2e-w19c-<run>-w<N>`），三臂互不共享任何记忆 / 画像；
  · **不沉淀**：session 必须是 `<user>-session-<n>`（网关硬校验），前缀 `e2e-` 落在 memory 服务
    `MEMORY_EXTRACT_SKIP_PREFIXES` 里 ⇒ 不做 LLM 抽取，臂内各组之间也不会互相喂记忆；
  · **单变量**：每轮 `meta.planner_history_exchanges=<N>`（批 5 W19 的请求级 pin），其余全同。

语料形态：T1 立一个**只活在对话历史里**的指代物（联网搜索主题 / 手册功能 / 球队——刻意避开焦点块
会自己记住的城市 / POI / 目的地 / 股票 / 车控对象 / 候选集），插 k 轮无关插话（闲聊 + 一轮 read 任务，
后者把 W07 活动任务帧顶掉——见 `_FILLERS_*` 的注释），最后一轮省略回指。
k=2 的组区分「2 对 vs 4 对」，k=4 的组区分「4 对 vs 6 对」。判据是**结构的**：最后一轮
`cloud.planning` 的计划槽里有没有指代物关键词（planner 自己把它填回去了），同时记
`history_pairs_kept`（证明 pin 生效）、intents、outcome，以及一条弱读数「话术里提到了没有」
（落到 chitchat 时它自己那 8 条历史也可能答对——那是另一条通道，不算 planner 解出）。

用法（真栈动作前先 `python scripts/dev_stack.py target show`；只准 cloud 档）：
    python scripts/probe_history_window.py --dry-run
    python scripts/probe_history_window.py --run 0920a --windows 2,4,6 --groups 1-8
    python scripts/probe_history_window.py --run 0920a --windows 6 --groups 9-16 --out <同一份 artifact>
    python scripts/probe_history_window.py --report <artifact.json>       # 纯本地汇总

同一个 `--run` 下分多次跑（每次 ≤ 10 分钟），artifact 按 (window, group) 合并；同一格重跑会覆盖。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import probe_qa_regression as probe                         # noqa: E402
from scripts.dev_stack_lib import read_root_env                          # noqa: E402
from scripts.e2e_identity import decode_secret, sign_identity            # noqa: E402
from scripts.e2e_target import endpoint_environment, resolve_e2e_target  # noqa: E402
from scripts.render_cloud_env import DEMO_AUTH_SCOPES                    # noqa: E402

#: 插话里必须有**任务轮**（天气 / 空气质量：read 任务、指代物不可能是它）：W07 的活动任务帧会把上一件
#: 任务的槽（`query=华为问界M9的售价`）在焦点块里保 1800 s，而 chitchat 是 response_only、不顶帧——
#: 只插闲聊的话，T1 的指代物会经任务帧而不是历史到达 planner（首跑 run 0920a：视窗 2 对下 g02 / g03
#: 的槽里照样出现 M9 / Model Y，pairs_kept=2、dropped=1）。让最后一个插话是任务轮，帧才被顶掉，
#: 历史视窗才是唯一变量。天气只改 `last_city` / `上一轮意图`，与本语料的指代物无关。
_FILLERS_K2 = ("讲个笑话", "深圳今天天气怎么样")
_FILLERS_K4 = ("讲个笑话", "深圳今天天气怎么样", "现在几点了", "深圳空气质量怎么样")

#: 32 组：(T1 指代物句, 最后一轮省略回指, 判据关键词, k)。关键词是指代物里最不会被改写掉的那一段。
GROUPS: list[dict] = [
    # ── k=2：区分 2 对 vs 4 对 ──
    {"t1": "帮我查一下小米SU7的官方续航是多少", "last": "那它的充电速度呢", "kw": "SU7", "k": 2},
    {"t1": "华为问界M9的售价是多少", "last": "那它的轴距呢", "kw": "M9", "k": 2},
    {"t1": "特斯拉Model Y现在卖多少钱", "last": "那它的加速成绩呢", "kw": "Model Y", "k": 2},
    {"t1": "自动泊车功能怎么开启", "last": "它支持垂直车位吗", "kw": "自动泊车", "k": 2},
    {"t1": "座椅加热在哪里打开", "last": "它有几档", "kw": "座椅加热", "k": 2},
    {"t1": "胎压报警灯亮了是什么意思", "last": "那正常值应该是多少", "kw": "胎压", "k": 2},
    {"t1": "曼城最近一场比赛结果", "last": "那下一场什么时候踢", "kw": "曼城", "k": 2},
    {"t1": "阿森纳上一轮联赛比分", "last": "他们现在排第几", "kw": "阿森纳", "k": 2},
    {"t1": "比亚迪海豹的电池容量是多少", "last": "那它百公里电耗呢", "kw": "海豹", "k": 2},
    {"t1": "查一下今年诺贝尔物理学奖得主是谁", "last": "他的主要研究方向是什么", "kw": "诺贝尔", "k": 2},
    {"t1": "极氪001的最新款什么时候上市", "last": "那它的起售价呢", "kw": "极氪", "k": 2},
    {"t1": "大疆最新的无人机型号是什么", "last": "它续航多久", "kw": "大疆", "k": 2},
    {"t1": "定速巡航怎么设置", "last": "它能在多少速度以上用", "kw": "定速巡航", "k": 2},
    {"t1": "远光灯自动切换功能怎么打开", "last": "它在什么条件下会自动关闭", "kw": "远光", "k": 2},
    {"t1": "国足最近一场比赛比分", "last": "那下一场对手是谁", "kw": "国足", "k": 2},
    {"t1": "查一下华为Mate 70的发布日期", "last": "那它的价格呢", "kw": "Mate", "k": 2},
    # ── k=4：区分 4 对 vs 6 对 ──
    {"t1": "小鹏MONA M03的续航是多少", "last": "那它的售价呢", "kw": "MONA", "k": 4},
    {"t1": "理想L9的油箱多大", "last": "那它的纯电续航呢", "kw": "L9", "k": 4},
    {"t1": "蔚来ET5的换电要多久", "last": "那它的电池容量呢", "kw": "ET5", "k": 4},
    {"t1": "车道保持辅助怎么打开", "last": "它在多少速度以下会失效", "kw": "车道保持", "k": 4},
    {"t1": "儿童安全锁在哪里", "last": "它锁上以后从车内还能开门吗", "kw": "儿童安全锁", "k": 4},
    {"t1": "无线充电板支持多大功率", "last": "它对手机壳厚度有要求吗", "kw": "无线充电", "k": 4},
    {"t1": "利物浦上一场英超比分", "last": "那他们下一场什么时候", "kw": "利物浦", "k": 4},
    {"t1": "皇马最近一场西甲结果", "last": "他们现在积分榜第几", "kw": "皇马", "k": 4},
    {"t1": "查一下OpenAI最新发布的模型叫什么", "last": "它的上下文长度是多少", "kw": "OpenAI", "k": 4},
    {"t1": "苹果最新的iPhone型号是什么", "last": "那它的起售价呢", "kw": "iPhone", "k": 4},
    {"t1": "智己LS6的零百加速多少", "last": "那它的续航呢", "kw": "LS6", "k": 4},
    {"t1": "查一下C罗现在在哪个俱乐部", "last": "他这个赛季进了几个球", "kw": "C罗", "k": 4},
    {"t1": "自适应巡航的跟车距离怎么调", "last": "它最多能设几档", "kw": "自适应巡航", "k": 4},
    {"t1": "方向盘加热怎么开", "last": "它多久会自动关", "kw": "方向盘加热", "k": 4},
    {"t1": "深圳湾大桥全长多少", "last": "它是哪一年通车的", "kw": "深圳湾大桥", "k": 4},
    {"t1": "港珠澳大桥全长多少公里", "last": "它的通行费是多少", "kw": "港珠澳", "k": 4},
]
assert len(GROUPS) == 32 and sum(1 for g in GROUPS if g["k"] == 2) == 16

_WINDOWS = (2, 4, 6)
_RUN_RE = re.compile(r"^[A-Za-z0-9._:-]{1,24}$")


def turns_of(group: dict) -> list[str]:
    fillers = _FILLERS_K2 if group["k"] == 2 else _FILLERS_K4
    return [group["t1"], *fillers, group["last"]]


def parse_groups(spec: str) -> list[int]:
    out: list[int] = []
    for part in (spec or "1-32").split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    bad = [n for n in out if not 1 <= n <= len(GROUPS)]
    if bad:
        raise SystemExit(f"组号越界：{bad}")
    return sorted(set(out))


def _keyword_in(value: str, kw: str) -> bool:
    return kw.lower() in (value or "").lower()


def plan_slots_text(plan_attr: str) -> str:
    """`cloud.planning` 的 `plan` 属性（步骤 JSON 串）→ 所有槽值拼成一段文本。"""
    try:
        steps = json.loads(plan_attr or "[]")
    except Exception:
        return ""
    parts: list[str] = []
    for step in steps if isinstance(steps, list) else []:
        slots = step.get("slots") if isinstance(step, dict) else None
        if isinstance(slots, dict):
            parts.extend(str(v) for v in slots.values())
    return " | ".join(parts)


def judge(final_detail: dict, group: dict, speech: str) -> dict:
    spans = [s for s in (final_detail.get("spans") or []) if isinstance(s, dict)]
    planning: dict = {}
    for s in spans:
        if s.get("node") == "cloud.planning":
            attrs = s.get("attrs") or {}
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except Exception:
                    attrs = {}
            planning = attrs
    turn = final_detail.get("turn") or {}
    slots = plan_slots_text(str(planning.get("plan") or ""))
    return {
        "slot_resolved": _keyword_in(slots, group["kw"]),
        "speech_mentions": _keyword_in(speech, group["kw"]),
        "slots": slots[:200],
        "intents": str(turn.get("intents") or ""),
        "outcome": str(turn.get("outcome") or ""),
        "plan_mode": str(planning.get("plan_mode") or ""),
        "history_pairs_kept": planning.get("history_pairs_kept"),
        "history_exchanges": planning.get("history_exchanges"),
        "trace_id": str(turn.get("trace_id") or ""),
    }


def _http_json(url: str, timeout: float = 30.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


async def fetch_final_detail(collector: str, session: str, last_text: str,
                             attempts: int = 10) -> dict:
    """按 session 找最后一轮（user_text 相同）的 trace 详情；collector 是异步落库，带重试。"""
    url = f"{collector}/api/sessions/{urllib.parse.quote(session, safe='')}/turns"
    for attempt in range(attempts):
        try:
            rows = await asyncio.to_thread(_http_json, url)
        except Exception:
            rows = []
        match = [r for r in rows if isinstance(r, dict) and r.get("user_text") == last_text]
        if match:
            tid = str(match[-1].get("trace_id") or "")
            try:
                detail = await asyncio.to_thread(
                    _http_json, f"{collector}/api/turns/{urllib.parse.quote(tid, safe='')}")
            except Exception:
                detail = {}
            spans = detail.get("spans") if isinstance(detail, dict) else None
            if spans and any(s.get("node") == "cloud.planning" for s in spans if isinstance(s, dict)):
                return detail
            if spans and attempt >= 3:        # 没有 planning span 的轮（确定性出口）也要收
                return detail
        await asyncio.sleep(1.0)
    return {}


def _endpoints() -> tuple[str, str, bytes]:
    env = dict(os.environ)
    env.update(read_root_env(_ROOT, {"TAILNET_FQDN", "VITE_WS_TOKEN", "E2E_IDENTITY_SECRET"}))
    target = resolve_e2e_target(_ROOT, explicit=None, environ=env)
    if target.name != "cloud":
        raise SystemExit("本实验只准 cloud 档（干净用户走云端网关的签名身份车道）")
    endpoints = endpoint_environment(target)
    raw = str(env.get("E2E_IDENTITY_SECRET") or "").strip()
    if not raw:
        raise SystemExit("根 .env 缺 E2E_IDENTITY_SECRET（签名身份车道未开）")
    return endpoints["WS_URL"], endpoints["COLLECTOR_URL"].rstrip("/"), decode_secret(raw)


def _ws_url_with(ws_url: str, token: str) -> str:
    parts = urllib.parse.urlsplit(ws_url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k != "token"]
    query.append(("token", token))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


async def run_group(ws_url: str, collector: str, secret: bytes, run: str,
                    window: int, index: int) -> dict:
    import websockets

    group = GROUPS[index - 1]
    run_id = f"e2e-w19c-{run}"
    user_id = f"{run_id}-w{window}"
    session = f"{user_id}-session-{index}"
    token = sign_identity(secret, run_id=run_id, user_id=user_id, vehicle_id="v1",
                          scopes=list(DEMO_AUTH_SCOPES), timeout_s=1800)
    started = time.time()
    rows: list[dict] = []
    async with websockets.connect(_ws_url_with(ws_url, token)) as ws:
        try:
            await asyncio.wait_for(ws.recv(), timeout=probe._HELLO_WAIT_S)
        except asyncio.TimeoutError:
            pass
        for turn_no, text in enumerate(turns_of(group), 1):
            try:
                obs = await probe._one_turn(
                    ws, session, text,
                    meta_overrides={"planner_history_exchanges": str(window)})
            except asyncio.TimeoutError:
                obs = {"speech": "[timeout]", "actions": [], "error": True}
            rows.append({"turn": turn_no, "say": text,
                         "speech": str(obs.get("speech") or ""),
                         "actions": list(obs.get("actions") or []),
                         "error": bool(obs.get("error"))})
            print(f"    w{window} g{index:02d} T{turn_no} {text[:22]:<22} → "
                  f"{rows[-1]['speech'][:46].replace(chr(10), ' ')}")
    final = rows[-1]
    detail = await fetch_final_detail(collector, session, final["say"])
    verdict = judge(detail, group, final["speech"])
    flag = "✔" if verdict["slot_resolved"] else "✘"
    print(f"  {flag} w{window} g{index:02d} k={group['k']} kw={group['kw']} "
          f"slot={verdict['slot_resolved']} speech={verdict['speech_mentions']} "
          f"intents={verdict['intents']} outcome={verdict['outcome']} "
          f"pairs_kept={verdict['history_pairs_kept']} exch={verdict['history_exchanges']}")
    return {"window": window, "group": index, "k": group["k"], "kw": group["kw"],
            "user_id": user_id, "session": session, "elapsed_s": round(time.time() - started, 1),
            "turns": rows, **verdict}


def load_artifact(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"ts": int(time.time()), "run": "", "cells": []}


def save_artifact(path: Path, artifact: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")


def report(artifact: dict) -> str:
    cells = artifact.get("cells") or []
    by = defaultdict(list)
    for c in cells:
        by[(c["k"], c["window"])].append(c)
    lines = ["| k | 视窗 | 组数 | planner 槽解出 | 话术提到 | 落 chitchat | pin 生效(pairs_kept) |",
             "|---|---|---|---|---|---|---|"]
    for k in (2, 4):
        for w in _WINDOWS:
            rows = by.get((k, w)) or []
            if not rows:
                continue
            solved = sum(1 for r in rows if r["slot_resolved"])
            spoken = sum(1 for r in rows if r["speech_mentions"])
            chit = sum(1 for r in rows if "chitchat" in (r["intents"] or ""))
            expected_pairs = k + 1 if w >= k + 1 else w
            kept = sum(1 for r in rows if r.get("history_pairs_kept") == expected_pairs)
            lines.append(f"| {k} | {w} 对 | {len(rows)} | **{solved}/{len(rows)}** | {spoken}/{len(rows)} "
                         f"| {chit}/{len(rows)} | {kept}/{len(rows)} (期望 {expected_pairs}) |")
    total = defaultdict(lambda: [0, 0])
    for c in cells:
        total[c["window"]][0] += int(bool(c["slot_resolved"]))
        total[c["window"]][1] += 1
    lines.append("")
    lines.append("合计（全部组）：" + "；".join(
        f"视窗 {w} 对 {total[w][0]}/{total[w][1]}" for w in _WINDOWS if total[w][1]))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="", help="本次实验标记（同一标记 = 同三个干净用户；分批跑必须相同）")
    ap.add_argument("--windows", default="2,4,6")
    ap.add_argument("--groups", default="1-32", help="组号，如 1-8 或 3,5,9-12")
    ap.add_argument("--out", default="", help="artifact 路径（缺省按 run 命名）")
    ap.add_argument("--dry-run", action="store_true", help="只列语料")
    ap.add_argument("--report", default="", help="只汇总一份已有 artifact")
    args = ap.parse_args()

    if args.report:
        print(report(load_artifact(Path(args.report))))
        return 0
    groups = parse_groups(args.groups)
    windows = sorted({int(x) for x in args.windows.split(",") if x.strip()})
    if any(w not in _WINDOWS for w in windows):
        raise SystemExit(f"视窗只认 {_WINDOWS}")
    if args.dry_run:
        for n in groups:
            g = GROUPS[n - 1]
            print(f"g{n:02d} k={g['k']} kw={g['kw']}: " + " / ".join(turns_of(g)))
        print(f"{len(groups)} 组 × {len(windows)} 臂 = {len(groups) * len(windows)} 个 session")
        return 0
    if not _RUN_RE.fullmatch(args.run or ""):
        raise SystemExit("--run 必填（1–24 位 [A-Za-z0-9._:-]），分批跑用同一个值")
    out = Path(args.out) if args.out else (
        _ROOT / ".artifacts" / "dev-stack-verifications" / f"w19c-history-window-{args.run}.json")
    ws_url, collector, secret = _endpoints()
    artifact = load_artifact(out)
    if artifact.get("run") and artifact["run"] != args.run:
        raise SystemExit(f"artifact 属于另一次 run（{artifact['run']}），换 --out")
    artifact["run"] = args.run
    artifact.setdefault("model_note", "LLM 未 pin：以 collector 的 provider/model 为准")
    print(f"真栈目标：cloud（{urllib.parse.urlsplit(ws_url).netloc}）  run={args.run}  "
          f"windows={windows}  groups={groups}")

    async def run_all():
        for w in windows:
            for n in groups:
                cell = await run_group(ws_url, collector, secret, args.run, w, n)
                artifact["cells"] = [c for c in artifact.get("cells") or []
                                     if not (c["window"] == w and c["group"] == n)] + [cell]
                save_artifact(out, artifact)

    asyncio.run(run_all())
    print()
    print(report(artifact))
    print(f"\nartifact：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
