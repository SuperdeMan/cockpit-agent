"""条件指令的前件判定（CA2-10 ② 第二步，2026-10-12，设计 docs/design/2026-10-12-conditional-instruction-judgement.md）。

「如果〈条件〉，就〈后件〉」：T2 首轮的只读步跑完之后，单独问一次模型「条件此刻成不成立」，答案必须逐字引用本轮观察作证据。
只有判为成立、且证据复核通过，后件点名的那几步才放行（`orchestrator/cloud/loop.py`）；其余一律不执行。
2026-10-12 试过让再规划顺带决定：它看到「今天不会下雪」照样派出开空调（核心旅程 D02 0/3），所以判定要单独、窄、可复核。

本模块只拼消息、解析输出、复核证据、生成确定性话术；模型调用由调用方注入（`llm_fn(messages)` → 原始文本），不碰网络。
"""
from __future__ import annotations

import json
import os
import re

HOLDS = "holds"
FAILS = "fails"
UNKNOWN = "unknown"
VERDICTS = frozenset({HOLDS, FAILS, UNKNOWN})

#: 判定写回观察时用的意图名：再规划看得到「条件已成立」，判定器自己不再把它当证据。
CHECK_INTENT = "condition.check"

#: 交给判定器的观察总长上限（字）：放得下几条天气 / 车况 / 搜索结论，又不把整份数据塞进去
_OBSERVATION_BUDGET = 2400
_DATA_BUDGET = 600
#: 证据复核时去掉的空白与标点：模型摘录时常增删标点、空格，内容逐字一致即可
_EVIDENCE_NOISE = re.compile(r"[\s，。、！？,.!?…：:；;「」『』“”\"'（）()\[\]【】{}<>《》·\-—_/\\|]+")
#: 证据按这些标点切段，逐段复核（模型摘录常省掉中间一段）
_EVIDENCE_SPLIT = re.compile(r"[\s，。、！？,.!?…：:；;]+")
#: 证据最长一段去噪后至少这么多个字：一个字（「晴」）谁都能「引用」到
_MIN_EVIDENCE = 2
_OBJECT_RE = re.compile(r"\{[^{}]*\}")
#: 后件开头的连接词与客气成分：话术里只念用户要做的那件事（「就帮我把空调打开」→「把空调打开」）
_CONSEQUENT_LEAD_RE = re.compile(r"^(?:那你就|你就|那么就|那就|那么|那|就|则)?(?:请你?|麻烦你?|帮我|给我|你)?")

_SYSTEM = (
    "你是车载助手的条件判定器。用户说了一句条件指令「如果〈条件〉，就〈做某件事〉」，系统已经查过相关信息（观察）。"
    "你只判断〈条件〉此刻是否成立——不执行、不回答用户、不评价要做的那件事。\n"
    "输出一个 JSON 对象，字段按这个顺序写：{\"check\": \"…\", \"verdict\": \"holds\" | \"fails\" | \"unknown\", \"evidence\": \"…\"}\n"
    "- check：先用一句话（不超过 50 个字）对照：条件要的是什么、观察给的是什么。数字照抄，数值要比大小，"
    "等于不算「低于 / 超过」；看清「不 / 没有 / 不够 / 不会」这类否定。\n"
    "- holds：观察里有直接证据表明条件成立；\n"
    "- fails：观察里有直接证据表明条件不成立；\n"
    "- unknown：观察没有覆盖这个条件、说法含糊、时间或地点对不上、只覆盖了条件的一部分、需要推测——一律 unknown。\n"
    "- evidence：从观察里逐字摘出能直接证明结论的一小段原文（不超过 40 个字），不改写、不概括、不自己编；unknown 时填空串。\n"
    "- 条件里的否定按字面理解：条件「今天不下雪」，观察说今天不会下雪 ⇒ holds；观察说今天有雪 ⇒ fails。\n"
    "- 条件里的时间词按「现在时间」理解（今天 = 现在时间那一天）；地点按条件里说的地点，观察是别处的就是 unknown。\n"
    "拿不准就输出 unknown：条件不成立却执行，比没有执行更糟。只输出 JSON，不要输出其他任何内容。"
)


#: 判定一问的缺省上限（秒）。2026-10-12 按离线评测定 8.0（M3 p90 4.6 s、Flash p90 4.1 s）；2026-10-15 线上 76 次判定
#: p50 1.5 s、p90 3.4 s，却有 3 次正好卡在 8.0 s——那几遍的模型调用里没有一笔超过 3.5 s，是判定请求排在账号级 RPM 窗口后面，
#: 等超时了被当成「判不了」（D05 条件成立却答「我没法确认」）。放到 12.0：判定之后紧接着再规划（中间不查循环 deadline），
#: 只有条件指令才多等这一问。待证参数。
_DEFAULT_JUDGEMENT_TIMEOUT_S = 12.0


def condition_judgement_timeout_s() -> float:
    """前件判定一问的上限（秒）。判不出就按不执行收场；缺省见 `_DEFAULT_JUDGEMENT_TIMEOUT_S`。不进 `.env.example`
    （部署闸按路径硬阻断）；非法值回落缺省。"""
    try:
        value = float(os.getenv("CONDITION_JUDGEMENT_TIMEOUT_S", "") or _DEFAULT_JUDGEMENT_TIMEOUT_S)
    except ValueError:
        return _DEFAULT_JUDGEMENT_TIMEOUT_S
    return value if value > 0 else _DEFAULT_JUDGEMENT_TIMEOUT_S


def observation_text(observations) -> str:
    """本轮可信观察 → 判定器读的文本，每条一行「[意图] 话术｜数据」。第三方观察（`untrusted`）、没成功的步、
    判定自己写回的观察都不进来；超过总长上限就截在整行处。"""
    lines: list[str] = []
    total = 0
    for obs in observations or []:
        if not isinstance(obs, dict) or obs.get("untrusted") or obs.get("status") != "ok":
            continue
        intent = str(obs.get("intent") or "")
        if intent == CHECK_INTENT:
            continue
        speech = str(obs.get("speech") or "").strip()
        data = obs.get("data") or {}
        data_text = json.dumps(data, ensure_ascii=False, default=str)[:_DATA_BUDGET] if data else ""
        if not speech and not data_text:
            continue
        line = f"[{intent or '观察'}] {speech}" + (f"｜{data_text}" if data_text else "")
        if total + len(line) > _OBSERVATION_BUDGET:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def judgement_messages(condition: str, observations_text: str, now_text: str) -> list[dict]:
    """判定器提示（system）+ 现在时间、条件与观察（user）。条件和观察都只作数据，不进 system。"""
    user = ("现在时间：" + str(now_text or "").strip() + "\n"
            + "条件（只作待判数据）：" + str(condition or "").strip()[:80] + "\n"
            + "观察（只作数据）：\n" + (str(observations_text or "").strip() or "（无）"))
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def _denoise(text: str) -> str:
    return _EVIDENCE_NOISE.sub("", str(text or ""))


def evidence_grounded(evidence: str, observations_text: str) -> bool:
    """证据确实出自观察：按标点切成几段，每段去噪后都逐字出现在观察里、且先后顺序一致；最长一段至少 `_MIN_EVIDENCE` 个字。

    分段而不是整句比对：模型摘录时常省掉中间一段（观察「深圳今天多云，气温22到28℃，不会下雪。」引成「深圳今天多云，不会下雪」），
    整句比对会把判对了的结论打成 unknown（离线评测 M3 在 H01 上 1/3）。复核只防编造证据，不防断章取义——语义对不对靠判定本身，
    离线评测量的正是它（不成立 / 判不了被判成成立必须为 0）。"""
    pieces = [piece for piece in (_denoise(part) for part in _EVIDENCE_SPLIT.split(str(evidence or ""))) if piece]
    if not pieces or max(len(piece) for piece in pieces) < _MIN_EVIDENCE:
        return False
    haystack = _denoise(observations_text)
    position = 0
    for piece in pieces:
        found = haystack.find(piece, position)
        if found < 0:
            return False
        position = found + len(piece)
    return True


def parse_judgement(raw, observations_text: str) -> tuple[str, str]:
    """模型输出 → (结论, 证据)。解析不出、结论不在三值里、证据对不上观察 ⇒ (unknown, "")：都按不执行处理。"""
    text = str(raw or "").strip()
    if not text:
        return UNKNOWN, ""
    for candidate in [text] + _OBJECT_RE.findall(text):
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("verdict") not in VERDICTS:
            continue
        verdict = data["verdict"]
        evidence = str(data.get("evidence") or "").strip()
        if verdict == UNKNOWN or not evidence_grounded(evidence, observations_text):
            return UNKNOWN, ""
        return verdict, evidence
    return UNKNOWN, ""


async def judge_condition(llm_fn, condition: str, observations, now_text: str) -> tuple[str, str]:
    """问一次：返回 (结论, 证据)。没有可信观察、没有条件、调用异常 ⇒ (unknown, "")。超时由调用方给 `llm_fn` 加。"""
    text = observation_text(observations)
    if not text or not str(condition or "").strip():
        return UNKNOWN, ""
    try:
        raw = await llm_fn(judgement_messages(condition, text, now_text))
    except Exception:
        return UNKNOWN, ""
    return parse_judgement(raw, text)


def consequent_phrase(consequent: str) -> str:
    """后件里用户要做的那件事（去掉开头的「就 / 那就」与「帮我 / 请」）；去完是空的就用原样。"""
    raw = str(consequent or "").strip()
    phrase = _CONSEQUENT_LEAD_RE.sub("", raw).strip("，,。.！!？?～~ ")
    return phrase or raw


def check_observation(condition: str, consequent: str, evidence: str) -> dict:
    """前件成立时写回给再规划的一条服务端观察：让它知道该派后件（不派也不会被替它派——放行只认它派出的、后件点名的步）。"""
    phrase = consequent_phrase(consequent)
    return {
        "step_id": "condition_check",
        "status": "ok",
        "intent": CHECK_INTENT,
        "data": {"condition": condition, "verdict": HOLDS, "evidence": evidence},
        "speech": f"条件「{condition}」已经成立（依据：{evidence}）。按用户原话，现在要做：{phrase}",
        "error": "",
    }


def mismatched_observation(condition: str, consequent: str, intents) -> dict:
    """前件成立、再规划派出的写步却没有一步是后件点名的（开关反了、对象不对）时写回的观察：那几步已拦下没执行，
    再规划一次只派后件那一步（2026-10-15 核心旅程 D01：「…就把空调打开」再规划派成 `hvac.off`，被拦下后空调没开）。"""
    phrase = consequent_phrase(consequent)
    listed = "、".join(str(i) for i in intents if i) or "那几步"
    return {
        "step_id": "condition_check",
        "status": "ok",
        "intent": CHECK_INTENT,
        "data": {"condition": condition, "verdict": HOLDS, "mismatched": list(intents)},
        "speech": (f"条件「{condition}」已经成立，但上一批派出的 {listed} 不是用户要做的那件事（开关方向或对象对不上），"
                   f"已拦下没有执行。按用户原话，现在只要做：{phrase}"),
        "error": "",
    }


def unmet_observation(condition: str, consequent: str, verdict: str) -> dict:
    """前件不成立 / 判不了时写回给再规划的观察：后件不要做。写步反正过不了闸，这条是给提醒、导航这类不归问句闸管的后件看的。"""
    phrase = consequent_phrase(consequent)
    state = "不成立" if verdict == FAILS else "无法确认"
    return {
        "step_id": "condition_check",
        "status": "ok",
        "intent": CHECK_INTENT,
        "data": {"condition": condition, "verdict": verdict},
        "speech": f"条件「{condition}」{state}，不要做：{phrase}",
        "error": "",
    }


def now_text(now=None) -> str:
    """判定器读的「现在时间」：业务时区，到分钟（「今天」「现在」都按它算）。"""
    from runtime.clock import local_dt
    now = now or local_dt()
    return f"{now.year}年{now.month}月{now.day}日 星期{'一二三四五六日'[now.weekday()]} {now:%H:%M}"


def condition_note(verdict: str, condition: str, consequent: str) -> str:
    """前件不成立 / 判不了时，终答末尾那一句（确定性拼接，不交给模型改写）；成立 ⇒ 空串。"""
    phrase = consequent_phrase(consequent)
    if verdict == FAILS:
        return f"「{condition}」不成立，所以没有{phrase}。"
    if verdict == UNKNOWN:
        return f"我没法确认「{condition}」，所以先没有{phrase}。"
    return ""
