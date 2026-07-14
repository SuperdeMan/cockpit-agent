"""场景编译器：自然语言 → Scene DSL（D2：**LLM 只在创建期当编译器，不当执行器**）。

「帮我建个钓鱼模式：座椅放平、开外循环、氛围灯调暗」→ LLM 产候选 JSON → 逐条过
`catalog.validate_action` 白名单校验 → 危险动作强制标 `require_confirm` → 回读确认后落库。
**激活/执行/修复期零 LLM**：同一场景每次执行结果必须确定可预期（规划/执行分离，CLAUDE.md §5）。

诚实纪律：LLM 编不出来的诉求（「放舒缓音乐」P0 不支持媒体）进 `dropped`，回读时明说
「这条我还做不到，已跳过」——**不静默丢**（用户确认时看到的动作执行时消失 = 信任崩塌）。
两次解析失败 → `ok=False` 诚实降级，不猜。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from .catalog import Catalog, catalog_digest, resolve_command, validate_action

logger = logging.getLogger("agent.scene.compiler")

# 「创建一个钓鱼模式」→ 钓鱼模式（先吃掉创建类动词与量词，否则会连「一个」一起截进名字）
_NAME_AFTER_VERB_RE = re.compile(
    r"(?:创建|新建|自定义|帮我建|建立|建个|建一个|做一个|做个|存成|存为|存一个|设一个|设个)"
    r"(?:一个|一|个)?\s*(?:名为|叫)?\s*[「\"']?([一-龥A-Za-z0-9]{1,8}?模式)")
_NAME_BARE_RE = re.compile(r"([一-龥A-Za-z0-9]{1,8}?模式)")
# 裸模式名前的噪声量词/动词（_NAME_BARE_RE 兜底路径用）
_NAME_NOISE_RE = re.compile(
    r"^(请|麻烦|帮我|给我|我要|我想|想|要|来个|来一个|开启|打开|进入|切换到|启动|退出|关闭|取消|结束"
    r"|一个|个|这个|那个)+")
# 创建句里的"非内容"词：剥掉后还剩东西才算用户真说了场景内容（extract_spec 用）
_CREATE_NOISE_RE = re.compile(
    r"(请|麻烦|帮我|给我|我要|我想|想|要|创建|新建|自定义|建立|建个|建一个|建|做个|做一个|做"
    r"|存成|存为|存|设个|设一个|设|叫做|叫|名为|一个|个|模式)")

_SYSTEM = "你是车载场景编译器。把用户的场景需求编译成结构化 JSON，只输出 JSON，不要解释。"

_FEWSHOT = """示例——用户说「建个观星模式：车里灯全关，座椅放倒，空调 22 度，放点音乐」，输出：
{"name":"观星模式","description":"氛围灯关闭 + 座椅放平 + 空调22度","goal":"停在野外舒服地看星星",
 "actions":[{"type":"vehicle.control","command":"ambient_light.close","params":{}},
  {"type":"vehicle.control","command":"seat.recline","params":{"position":"front_left","angle":"170"}},
  {"type":"vehicle.control","command":"hvac.set","params":{"temperature":"22"}}],
 "unsupported":["放点音乐"]}"""


@dataclass
class Draft:
    """编译产物。ok=False 时 error 是给用户的诚实原因。"""
    name: str = ""
    description: str = ""
    goal: str = ""
    actions: list = field(default_factory=list)     # 已过白名单校验的干净动作
    dropped: list = field(default_factory=list)     # 剔除/降级项（回读时诚实告知）
    notes: list = field(default_factory=list)       # 参数夹紧等提示
    ok: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "goal": self.goal,
                "actions": self.actions, "dropped": self.dropped, "notes": self.notes}

    @classmethod
    def from_dict(cls, d: dict) -> "Draft":
        return cls(name=d.get("name", ""), description=d.get("description", ""),
                   goal=d.get("goal", ""), actions=d.get("actions") or [],
                   dropped=d.get("dropped") or [], notes=d.get("notes") or [],
                   ok=bool(d.get("actions")))


def extract_scene_name(text: str) -> str:
    """从原话里抠场景名（「帮我创建一个钓鱼模式：…」→ 钓鱼模式）。抠不出 → 空串。

    route_hints 的 `$text` 会把整句灌进槽位，所以 activate/create/deactivate 都要过这一层。
    """
    text = (text or "").strip()
    if not text:
        return ""
    m = _NAME_AFTER_VERB_RE.search(text)
    if m:
        return m.group(1)
    head = text.split("：")[0].split(":")[0]
    m = _NAME_BARE_RE.search(_NAME_NOISE_RE.sub("", head)) or \
        _NAME_BARE_RE.search(_NAME_NOISE_RE.sub("", text))
    return m.group(1) if m else ""


def extract_spec(text: str, name: str = "") -> str:
    """场景内容部分（冒号后的动作描述）。**没有实质内容 → 空串**（调用方据此追问）。

    「帮我建个钓鱼模式」剥掉名字和创建类动词后什么都不剩 → 空串（要追问「里面要做什么」）；
    「建个钓鱼模式：座椅放平」→「座椅放平」。
    """
    text = (text or "").strip()
    body = text
    for sep in ("：", ":", "，做", "，要"):
        if sep in text:
            body = text.split(sep, 1)[1].strip()
            break
    probe = body.replace(name, "") if name else body
    probe = _CREATE_NOISE_RE.sub("", probe)
    probe = re.sub(r"[，,。.！!？?、；;\s]", "", probe)
    return body if len(probe) >= 2 else ""


def _extract_json_block(text: str) -> str:
    """从 LLM 输出抠出第一个 {...}（容忍 ``` 包裹与前后噪声；trip pipeline 同款）。"""
    if not text:
        return ""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t).rstrip("` \n")
    start, end = t.find("{"), t.rfind("}")
    return t[start:end + 1] if start != -1 and end > start else ""


def _prompt(cat: Catalog, text: str, name_hint: str) -> str:
    hint = f"\n场景名已定：{name_hint}（沿用它，不要改）" if name_hint else ""
    return f"""可用车控白名单（command 只能从这里选；params 只能用列出的参数名与取值范围）：
{catalog_digest(cat)}

另外可用导航动作：{{"type":"navigate","payload":{{"destination":"家"}}}}

{_FEWSHOT}

用户说：「{text}」{hint}

按同样的 JSON 结构输出：
- name：场景名（简短，以「模式」结尾）
- description：一句话说明这个场景做什么
- goal：用户想达到的目标
- actions：有序动作数组；command 必须命中白名单（座椅放平用 seat.recline）；
  params 只能用该对象列出的参数名，数值在给出的范围内；不确定的参数就不写
- unsupported：白名单里做不到的用户诉求（原话），**禁止为它编造 command**
只输出 JSON。"""


async def compile_scene(llm, cat: Catalog, text: str, *, name_hint: str = "",
                        model: str = "", timeout: float = 30.0) -> Draft:
    """NL → Draft。LLM 产候选、确定性校验裁决（LLM 说了不算）。"""
    raw = ""
    data: dict | None = None
    for attempt in (1, 2):
        try:
            raw = await llm.complete(
                [{"role": "system", "content": _SYSTEM},
                 {"role": "user", "content": _prompt(cat, text, name_hint)}],
                model=model, temperature=0.0 if attempt == 1 else 0.3,
                max_tokens=900, timeout=timeout, thinking=False)
            parsed = json.loads(_extract_json_block(raw))
            if isinstance(parsed, dict):
                data = parsed
                break
        except Exception as e:                       # 网络/超时/JSON 均在此收口
            logger.warning("scene compile attempt %d failed: %s", attempt, e)
    if data is None:
        return Draft(ok=False, error="没太听懂这个场景要做什么，换个说法再讲一遍？")

    return build_draft(data, cat, text=text, name_hint=name_hint)


def build_draft(data: dict, cat: Catalog, *, text: str = "", name_hint: str = "") -> Draft:
    """LLM 候选 JSON → 校验后的 Draft（纯函数，可离线测；LLM 的声明一律不作数）。"""
    d = Draft()
    d.name = (name_hint or str(data.get("name") or "").strip()
              or extract_scene_name(text) or "").strip()
    if not d.name:
        return Draft(ok=False, error="这个场景叫什么名字？比如「钓鱼模式」。")
    d.description = str(data.get("description") or "").strip()
    d.goal = str(data.get("goal") or "").strip()

    for a in (data.get("actions") or []):
        ok, cleaned, reason = validate_action(a, cat)
        if not ok:
            d.dropped.append(reason or "有个动作我做不到")
            continue
        d.actions.append(cleaned)
        if reason:
            d.notes.append(reason)                   # 参数被夹紧等提示
    for u in (data.get("unsupported") or []):
        u = str(u).strip()
        if u:
            d.dropped.append(f"「{u}」我还做不到")

    if not d.actions:
        why = "、".join(d.dropped[:3]) if d.dropped else "没解析出可执行的动作"
        return Draft(ok=False, name=d.name, dropped=d.dropped,
                     error=f"这个场景我建不了：{why}。换个说法或换些能做的动作？")
    if not d.description:
        d.description = "、".join(action_desc(a) for a in d.actions[:3])
    d.ok = True
    return d


# ── 动作的人类可读描述（回读/卡片/话术共用一处，防三处漂移）─────────────────

# 按**解析后的规范** (object, operate) 索引——不按 command 原文：同一个动作 LLM 可能写
# hvac.set 也可能写 aircon.set、fragrance.on 也可能写 fragrance.open，按原文查表会漏成兜底
# （真栈实测渲染出「aircon.set（temperature=22）」）。
_CMD_DESC = {
    ("aircon", "set"): "空调设到{temperature}度",
    ("aircon", "open"): "打开空调", ("aircon", "close"): "关闭空调",
    ("ambient_light", "set"): "氛围灯{brightness}%",
    ("ambient_light", "open"): "打开氛围灯", ("ambient_light", "close"): "关闭氛围灯",
    ("volume", "set"): "音量调到{level}",
    ("fragrance", "open"): "打开香氛", ("fragrance", "close"): "关闭香氛",
    ("window", "open"): "打开车窗", ("window", "close"): "关闭车窗",
    ("sunroof", "open"): "打开天窗", ("sunroof", "close"): "关闭天窗",
    ("sunshade", "open"): "打开遮阳帘", ("sunshade", "close"): "关闭遮阳帘",
    ("trunk", "open"): "打开后备箱",
    ("door_lock", "open"): "解锁车门", ("door_lock", "close"): "锁车门",
    ("headlight", "open"): "打开大灯", ("headlight", "close"): "关闭大灯",
    ("screen", "set"): "屏幕亮度{brightness}%",
    ("driving_mode", "set"): "切到{mode}驾驶模式",
    ("power_mode", "set"): "切到{mode}动力模式",
    ("air_purifier", "open"): "打开空气净化", ("air_purifier", "close"): "关闭空气净化",
    ("wiper", "open"): "打开雨刮", ("wiper", "close"): "关闭雨刮",
    ("music", "play"): "播放音乐", ("music", "pause"): "暂停音乐",
    ("music", "close"): "关闭音乐",
    ("media", "play"): "播放媒体", ("media", "start"): "播放媒体",
    ("media", "pause"): "暂停播放", ("media", "close"): "关闭媒体",
    ("radio", "open"): "打开收音机", ("radio", "close"): "关闭收音机",
    ("scene_mode", "set"): "标记场景状态",
}
_SEAT_RECLINE_DESC = "座椅放平到{angle}度"
# 量级参数别名：模板要 brightness 而动作里是 level 时互相顶上（catalog 已归一，这是双保险）
_MAG_ALIASES = ("brightness", "level", "temperature", "angle", "value")


def action_desc(a: dict) -> str:
    """一条动作的中文描述。模板缺失/参数填不满时退可读兜底，不抛错、不渲染成「氛围灯%」。"""
    if (a.get("type") or "") == "navigate":
        return f"导航去{(a.get('payload') or {}).get('destination', '目的地')}"
    cmd = str(a.get("command") or "")
    params = a.get("params") or {}
    r = resolve_command(cmd)
    tmpl = ""
    if r:
        obj, operate, _attr, path_mode = r
        mode = params.get("mode", path_mode)
        if obj == "seat" and mode in ("recline", "放平", "座椅放平"):
            tmpl = _SEAT_RECLINE_DESC
        else:
            tmpl = _CMD_DESC.get((obj, operate), "")
    if tmpl:
        fields = re.findall(r"\{(\w+)\}", tmpl)
        if not fields:
            return tmpl
        vals = {}
        for k in fields:
            v = params.get(k, "")
            if not v and k in _MAG_ALIASES:            # 模板要 brightness、动作给的是 level
                v = next((params[x] for x in _MAG_ALIASES if params.get(x)), "")
            vals[k] = v
        if all(vals.values()):                         # 槽位填不满 → 别渲染成「氛围灯%」
            return tmpl.format(**vals)
    bits = "、".join(f"{k}={v}" for k, v in params.items() if k != "command")
    return f"{cmd}" + (f"（{bits}）" if bits else "")


def actions_preview(actions: list) -> list[dict]:
    """scene_card 的 actions_preview（label + danger 标记）。"""
    return [{"label": action_desc(a), "danger": bool(a.get("require_confirm"))}
            for a in actions]
