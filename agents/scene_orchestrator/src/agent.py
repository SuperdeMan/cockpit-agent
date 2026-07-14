"""场景编排 Agent（scene-orchestrator）——用户造场景 + 确定性执行。

产品形态（设计 `docs/design/2026-07-14-scene-orchestrator-redesign.md`）：
- **创建期 LLM 当编译器**（`scene.create`：一句话 → Scene DSL → 白名单校验 → 回读确认 → 落 PG）
- **激活/退出期零 LLM**：动作走既有确定性链路（AgentResult.actions → 端侧 `_dispatch_cloud_actions`
  → VAL 归一/校验/安全门控），同一场景每次执行结果确定可预期（规划/执行分离，CLAUDE.md §5）。

与 Planner 的边界（D11）：临时目标句（「我有点困想睡会」）归 Cloud Planner 现场编排；
本 Agent 只接**命名场景**（「X模式」），负责"每一次的可靠"。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from difflib import get_close_matches

import yaml

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED, NEED_CONFIRM
from agents._sdk.shared_state import SCENE_ACTIVE, SCENE_PENDING

from .catalog import affected_state_keys, is_danger, load_catalog, resolve_command, \
    restore_action, validate_action
from .compiler import Draft, action_desc, actions_preview, compile_scene, \
    extract_scene_name, extract_spec
from .state_mirror import StateMirror
from .store import BUILTIN, DISABLED, ENABLED, Scene, SceneStore, USER

logger = logging.getLogger("agent.scene_orchestrator")

_HERE = os.path.dirname(os.path.dirname(__file__))
_MANIFEST = os.path.join(_HERE, "manifest.yaml")
_SCENES_PATH = os.path.join(_HERE, "scenes.yaml")

# D8：端侧模式词（driving_mode / power_mode 走端侧 LOCAL_INTENTS 毫秒级秒回）——
# 用户不能拿这些名字造场景，否则同名遮蔽会把端侧秒回劫持成云端往返。
_EDGE_MODE_NAMES = re.compile(
    r"^(驾驶|运动|舒适|经济|节能|标准|雪地|越野|性能|省电|电量|动能回收|飞行|勿扰|静音)模式$")
# P1 会话沉淀（D11 桥）：「把刚才这些存成加班模式」——指代最近做过的操作，不是当场描述动作
_SEDIMENT_RE = re.compile(r"刚才|刚刚|这些|这样|现在这|当前这")


def _load_builtin(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("scenes", {}) or {}


class SceneOrchestratorAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.catalog = load_catalog()
        self.store = SceneStore()
        self.mirror = StateMirror()
        self._builtin: list[Scene] = self._builtin_scenes(_load_builtin(_SCENES_PATH))

    # ── 生命周期 ────────────────────────────────────────────────────────────
    async def on_start(self) -> None:
        await self.store.init()
        await self.mirror.start()

    def _builtin_scenes(self, raw: dict) -> list[Scene]:
        """预置场景（scenes.yaml）→ 统一 Scene 对象。危险标注同样经 §8.1 强制改写。"""
        out: list[Scene] = []
        for key, s in raw.items():
            actions = [self._normalize_builtin_action(a) for a in (s.get("actions") or [])]
            out.append(Scene(
                id=key, user_id="", name=s.get("name", key),
                aliases=list(s.get("aliases") or []),
                description=s.get("description", ""), source=BUILTIN,
                actions=[a for a in actions if a]))
        return out

    def _normalize_builtin_action(self, a: dict) -> dict | None:
        if (a.get("type") or "") == "navigate":
            return {"type": "navigate", "payload": dict(a.get("payload") or {}),
                    "require_confirm": False}
        cmd = a.get("command") or ""
        r = resolve_command(cmd)
        if not r:
            logger.warning("预置场景动作无法解析，已跳过：%s", cmd)
            return None
        params = {k: str(v) for k, v in (a.get("params") or {}).items()}
        return {"type": "vehicle.control", "command": cmd, "params": params,
                "require_confirm": is_danger(r[0], r[1], params.get("mode", r[3]),
                                             self.catalog)}

    # ── 分发 ────────────────────────────────────────────────────────────────
    # 话术纪律：**面向用户的诚实拒绝一律用 OK 状态**（「没找到这个场景」「这个名字被占了」）。
    # FAILED 只留给真·内部错误——聚合器对 FAILED 只取 `error` 码拼「抱歉，处理失败」，
    # Agent 的 speech 会被整个丢掉（orchestrator/cloud/aggregator.py:119-121），
    # 诚实话术走 FAILED 反而变成最不诚实的那句（2026-07-14 真栈 e2e 实测命中）。
    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "scene.create": self._create,
            "scene.activate": self._activate,
            "scene.deactivate": self._deactivate,
            "scene.update": self._update,
            "scene.delete": self._delete,
            "scene.list": self._list_scenes,
        }
        h = handlers.get(intent.name)
        if not h:
            return AgentResult(status=FAILED, speech="场景助手暂不支持该请求。")
        return await h(intent, ctx, meta or {})

    @staticmethod
    def _uid(ctx) -> str:
        return ctx.user_id or "u1"

    # ── 场景集合（用户场景遮蔽同名预置，D4）─────────────────────────────────
    async def _user_scenes(self, ctx) -> list[Scene]:
        """本用户的全部场景记录（含 disabled——遮蔽计算要用，见 _all_scenes）。"""
        try:
            return await self.store.list(self._uid(ctx), statuses=(ENABLED, DISABLED))
        except Exception as e:
            logger.warning("scene: 读用户场景失败（只用预置）：%s", e)
            return []

    async def _all_scenes(self, ctx) -> tuple[list[Scene], list[Scene]]:
        """(可用的用户场景, 未被遮蔽的预置场景)。

        遮蔽集合要含 **disabled** 条目：「删掉浪漫模式」删不掉预置（随镜像发版），落的是一条
        同名 disabled 记录来遮蔽它——若只用 enabled 算遮蔽，那条记录就白写了，预置还会冒出来。
        """
        all_mine = await self._user_scenes(ctx)
        taken = {s.name for s in all_mine}
        mine = [s for s in all_mine if s.status == ENABLED and s.source != BUILTIN]
        return mine, [b for b in self._builtin if b.name not in taken]

    async def _match(self, ctx, query: str) -> Scene | None:
        """合并匹配：用户场景（精确 id/名/别名 → 模糊）优先，再预置（同序）。

        route_hints 的 `$text` 会把整句灌进 scene 槽（「开启钓鱼模式」），先抠出场景名再匹配。
        """
        query = (query or "").strip()
        if not query:
            return None
        name = extract_scene_name(query) or query
        mine, builtin = await self._all_scenes(ctx)
        for pool in (mine, builtin):
            hit = self._match_in(pool, name) or self._match_in(pool, query)
            if hit:
                return hit
        return None

    @staticmethod
    def _match_in(pool: list[Scene], q: str) -> Scene | None:
        ql = q.strip().lower()
        if not ql:
            return None
        for s in pool:
            if s.id.lower() == ql or s.name.lower() == ql:
                return s
        for s in pool:
            if ql in [a.lower() for a in (s.aliases or [])]:
                return s
        names = [s.name for s in pool]
        m = get_close_matches(q, names, n=1, cutoff=0.6)   # cutoff 不放宽：宁追问不误激活
        if m:
            return next(s for s in pool if s.name == m[0])
        return None

    @staticmethod
    def _mode_key(s: Scene) -> str:
        """写进 VAL `scene_mode` 状态位的值：预置用场景键（camping/nap…），用户场景用名字
        （usr-xxxx 这种 id 对状态镜像/右舞台毫无意义）。"""
        return s.id if s.source == BUILTIN else s.name

    # ── scene.create：编译闭环（D2/D3）───────────────────────────────────────
    async def _create(self, intent, ctx, meta) -> AgentResult:
        raw = (intent.raw_text or "").strip()
        pend = await self._load_kv(ctx, SCENE_PENDING)
        confirmed = str(meta.get("confirmed", "")).lower() == "true"

        # 确认轮：草案早已编译好存在 pending 里——**不重跑 LLM**（重编译可能产出与用户
        # 确认时看到的不一样的动作，那是信任崩塌）。engine 的 _restore 对 NEED_SLOT 续接
        # 也会注入 confirmed=true，故必须以「pending 里有没有 draft」为准，不能只看 confirmed。
        if confirmed and pend.get("draft"):
            draft = Draft.from_dict(pend["draft"])
            if not draft.ok:
                await self._save_kv(ctx, SCENE_PENDING, {})
                return AgentResult(speech="刚才那个场景没存成，重新说一遍吧。")
            return await self._persist(ctx, draft, overwrite=bool(pend.get("overwrite")))

        name = (intent.slots.get("name") or "").strip()
        if not name or name == raw:
            name = extract_scene_name(raw)
        name = name or (pend.get("name") or "")
        spec = (intent.slots.get("spec") or "").strip()
        if not spec or spec == raw:
            spec = extract_spec(raw, name)

        if not name:
            await self._save_kv(ctx, SCENE_PENDING, {"spec": spec})
            return AgentResult(status=NEED_SLOT, speech="这个场景叫什么名字？",
                               follow_up="比如「钓鱼模式」「观星模式」",
                               missing_slots=["name"])
        if _EDGE_MODE_NAMES.match(name):
            return AgentResult(
                speech=f"「{name}」是车上本来就有的模式，不能用它当场景名。换一个吧，"
                       f"比如「钓鱼模式」。")

        # P1 会话沉淀（D11）：「把刚才这些存成加班模式」——内容不在这句话里，在**最近做过的
        # 操作**里。读 history 拼成 spec，走同一条编译+校验+回读闭环（不新增第二条创建路径）。
        # 这是「临时智能 → 沉淀 → 可靠复用」三段桥的中间一跳：Planner 负责第一次的聪明，
        # 场景负责每一次的可靠。
        # 判据不能只看 `not spec`：「把刚才这些存成加班模式」剥掉名字后还剩「把刚才这些」，
        # extract_spec 会把这句**元指令本身**当成 spec 喂给编译器（LLM 只能瞎猜）。
        if _SEDIMENT_RE.search(raw) and (not spec or _SEDIMENT_RE.search(spec)):
            spec = await self._history_spec(ctx)
            if not spec:
                return AgentResult(
                    speech=f"我没找到刚才做过的车内操作，没法存成{name}。"
                           f"直接说「创建{name}：座椅放平、空调22度」也行。")

        # 只说了名字没说内容（「帮我建个钓鱼模式」）→ 追问（名字存 pending，下轮续接）
        if not spec:
            await self._save_kv(ctx, SCENE_PENDING, {"name": name})
            return AgentResult(status=NEED_SLOT, speech=f"{name}里要做哪些事？",
                               follow_up="比如：座椅放平、空调22度、氛围灯调暗",
                               missing_slots=["spec"])
        if pend.get("name") and pend["name"] != name and not intent.slots.get("name"):
            name = pend["name"]                       # 续接轮：沿用上一轮定的名字

        existing = await self.store.get_by_name(self._uid(ctx), name)
        draft = await compile_scene(self.llm, self.catalog, spec or raw, name_hint=name,
                                    model=os.getenv("LLM_MODEL_SCENE", ""))
        if not draft.ok:
            await self._save_kv(ctx, SCENE_PENDING, {})
            return AgentResult(speech=draft.error)

        await self._save_kv(ctx, SCENE_PENDING,
                            {"name": name, "spec": spec, "draft": draft.to_dict(),
                             "overwrite": bool(existing)})
        lead = f"要把已有的{name}改成这样吗" if existing else f"将创建{name}，共 {len(draft.actions)} 个动作"
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"{lead}：{self._preview_speech(draft.actions)}。"
                   f"{self._dropped_speech(draft)}保存吗？",
            follow_up="说「确认」就存下来",
            ui_card=self._scene_card("confirm", name, draft.description, draft.actions))

    async def _persist(self, ctx, draft: Draft, overwrite: bool) -> AgentResult:
        s = Scene(user_id=self._uid(ctx), name=draft.name, description=draft.description,
                  goal=draft.goal, source=USER, actions=draft.actions,
                  aliases=self._aliases_for(draft.name))
        try:
            saved = await self.store.save(s)
        except Exception as e:
            logger.warning("scene: 落库失败：%s", e)
            await self._save_kv(ctx, SCENE_PENDING, {})
            return AgentResult(speech="场景没存上，稍后再试一次？")
        await self._save_kv(ctx, SCENE_PENDING, {})
        verb = "改好了" if overwrite else "建好了"
        return AgentResult(
            speech=f"{saved.name}{verb}。以后说「开启{saved.name}」就行。",
            ui_card=self._scene_card("created", saved.name, saved.description, saved.actions),
            data={"scene_id": saved.id})

    @staticmethod
    def _aliases_for(name: str) -> list[str]:
        """「钓鱼模式」→ 别名「钓鱼」，让「开启钓鱼」也能命中。"""
        base = name[:-2] if name.endswith("模式") else name
        return sorted({name, base} - {""})

    async def _history_spec(self, ctx, last_n: int = 8) -> str:
        """会话沉淀：把最近几轮的车内操作拼成 spec 交编译器。

        用户话 + 助手回执都带上——回执（「已为您打开空调，设定26度」）才是**实际发生了什么**，
        用户话可能含指代/口误。当前这轮不在 history 里（编排在本轮结束后才落库），天然干净。
        """
        try:
            turns = await ctx.history(last_n)
        except Exception as e:
            logger.debug("scene: 读 history 失败：%s", e)
            return ""
        lines = []
        for t in turns or []:
            text = (t.get("text") or "").strip()
            if not text or _SEDIMENT_RE.search(text):      # 跳过"存成X模式"这类元指令
                continue
            who = "用户" if t.get("role") == "user" else "已执行"
            lines.append(f"{who}：{text[:60]}")
        if not lines:
            return ""
        return "把最近这些车内操作固化成一个场景：\n" + "\n".join(lines[-6:])

    # ── scene.activate ──────────────────────────────────────────────────────
    async def _activate(self, intent, ctx, meta) -> AgentResult:
        query = (intent.slots.get("scene") or "").strip() or (intent.raw_text or "")
        if not query.strip():
            return AgentResult(status=NEED_SLOT, speech="您想开启哪个场景？",
                               follow_up="可以说「回家模式」「露营模式」，或者「有哪些场景」",
                               missing_slots=["scene"])
        scene = await self._match(ctx, query)
        if not scene:
            mine, builtin = await self._all_scenes(ctx)
            names = "、".join(s.name for s in (mine + builtin)[:6]) or "暂无"
            asked = extract_scene_name(query) or query
            return AgentResult(
                speech=f"没有找到「{asked}」。现在有：{names}。想新建一个就说"
                       f"「创建{asked}：要做的事」。")

        actions = [dict(a) for a in scene.actions]
        # P1 custom_params：「开启午休模式，温度26」——原话里的数值确定性覆盖同对象动作
        # （只覆盖已有动作、不新增；解析不出就忽略，**不 LLM 兜底**）。
        # 覆盖源要用 slots+raw_text 合并：确认轮的 raw_text 是「确认」，数值只在 slots 里
        # （route_hint 的 `scene: $text` 灌的是原句），只看 raw_text 会把覆盖弄丢。
        override_src = f"{query} {intent.raw_text or ''}"
        actions, overridden = self._apply_param_override(actions, override_src)

        confirmed = str(meta.get("confirmed", "")).lower() == "true"
        danger = [a for a in actions if a.get("require_confirm")]
        if danger and not confirmed:
            desc = "、".join(action_desc(a) for a in danger)
            return AgentResult(
                status=NEED_CONFIRM,
                speech=f"即将开启{scene.name}。其中{desc}需要您确认。确认执行吗？",
                follow_up="说「确认」即可",
                ui_card=self._scene_card("confirm", scene.name, scene.description, actions),
            ).action("scene.activate",
                     {"scene": scene.id, "actions": self._dispatch_payloads(actions)},
                     require_confirm=True)

        res = await self._dispatch(ctx, scene, actions)
        if overridden and res.status == "ok":
            res.speech = f"已为您开启{scene.name}，{self._preview_speech(actions, limit=3)}。"
        return res

    async def _dispatch(self, ctx, scene: Scene, actions: list) -> AgentResult:
        """下发动作：先按本次动作集采快照（D5 恢复基准）+ 写 SCENE_ACTIVE，再产出 actions。"""
        keys: set[str] = set()
        for a in actions:
            keys.update(affected_state_keys(a))
        snapshot = self.mirror.capture(sorted(keys))     # 读不到的键记 None → 退默认表

        activation_id = uuid.uuid4().hex
        await self._save_kv(ctx, SCENE_ACTIVE, {
            "scene_id": scene.id, "scene_name": scene.name,
            "activated_at": int(time.time()), "activation_id": activation_id,
            "snapshot": snapshot,
            "solved_actions": actions,        # 本次实际下发集 = 恢复基准（v2.1 修正④）
            "deferred": [],
        })
        if scene.source == USER:
            await self.store.bump_use(self._uid(ctx), scene.id)

        res = AgentResult(speech=f"已为您开启{scene.name}。",
                          ui_card=self._scene_card("activated", scene.name,
                                                   scene.description, actions))
        for a in actions + [self._scene_mode_action(self._mode_key(scene))]:
            res.action(*self._to_result_action(a))
        return res

    @staticmethod
    def _scene_mode_action(mode: str) -> dict:
        """场景状态位（硬伤 6）：车辆状态镜像/右舞台据此知道"当前在哪个场景"。"""
        return {"type": "vehicle.control", "command": "scene_mode.set",
                "params": {"mode": mode}, "require_confirm": False}

    @staticmethod
    def _to_result_action(a: dict) -> tuple[str, dict, bool]:
        """DSL 动作 → AgentResult.action(type, payload, require_confirm)。

        vehicle.control 的 command 必须并入 payload——VAL 经 payload["command"] 取指令
        （server.py `_dispatch_cloud_actions`），丢掉它动作即不可执行；且空 payload 的
        vehicle.control（如 fragrance.on）会被 Executor 的 action 校验直接丢弃。
        """
        if (a.get("type") or "") == "navigate":
            return "navigate", dict(a.get("payload") or {}), False
        payload = {"command": a["command"], **{k: str(v) for k, v in
                                               (a.get("params") or {}).items()}}
        return "vehicle.control", payload, bool(a.get("require_confirm"))

    def _dispatch_payloads(self, actions: list) -> list[dict]:
        out = []
        for a in actions:
            t, p, rc = self._to_result_action(a)
            out.append({"type": t, "payload": p, "require_confirm": rc})
        return out

    # ── scene.deactivate：真恢复（D5）───────────────────────────────────────
    async def _deactivate(self, intent, ctx, meta) -> AgentResult:
        active = await self._load_kv(ctx, SCENE_ACTIVE)
        if not active.get("scene_id"):
            return AgentResult(speech="当前没有开启场景模式。")

        name = active.get("scene_name") or "场景"
        snapshot = active.get("snapshot") or {}
        solved = active.get("solved_actions") or []

        restores: list[dict] = []
        notes: list[str] = []
        for a in solved:                       # 恢复基准 = 本次实际下发集（v2.1 修正④）
            r, note = restore_action(a, snapshot, self.catalog)
            if r:
                restores.append(r)
            elif note:
                notes.append(note)
        restores.append(self._scene_mode_action("off"))

        danger = [a for a in restores if a.get("require_confirm")]
        confirmed = str(meta.get("confirmed", "")).lower() == "true"
        if danger and not confirmed:
            desc = "、".join(action_desc(a) for a in danger)
            return AgentResult(
                status=NEED_CONFIRM,
                speech=f"将退出{name}，把{desc}。确认吗？",
                follow_up="说「确认」即可",
            ).action("scene.deactivate", {"scene": active["scene_id"],
                                          "actions": self._dispatch_payloads(restores)},
                     require_confirm=True)

        await self._save_kv(ctx, SCENE_ACTIVE, {})
        tail = ("（" + "；".join(dict.fromkeys(notes)) + "）") if notes else ""
        res = AgentResult(speech=f"已退出{name}，车内恢复原样。{tail}")
        for a in restores:
            res.action(*self._to_result_action(a))
        return res

    # ── scene.update / scene.delete ─────────────────────────────────────────
    async def _update(self, intent, ctx, meta) -> AgentResult:
        raw = (intent.raw_text or "").strip()
        query = (intent.slots.get("scene") or "").strip() or raw
        scene = await self._match(ctx, query)
        if not scene:
            return AgentResult(speech="没找到要改的场景。说「有哪些场景」我给你列一下。")
        if scene.source == BUILTIN:
            return AgentResult(
                speech=f"{scene.name}是内置场景，改不了。你可以说「创建{scene.name}："
                       f"（你要的动作）」建一个自己的同名场景，之后就以你的为准。")

        mod = (intent.slots.get("modification") or "").strip() or raw
        confirmed = str(meta.get("confirmed", "")).lower() == "true"
        pend = await self._load_kv(ctx, SCENE_PENDING)
        if confirmed and pend.get("draft"):
            draft = Draft.from_dict(pend["draft"])
            return await self._persist(ctx, draft, overwrite=True)

        # 参数级改动（「温度改成24」）：确定性覆盖已有动作，不惊动 LLM
        patched, changed = self._apply_param_override(list(scene.actions), mod)
        if changed:
            s = Scene(user_id=self._uid(ctx), name=scene.name, description=scene.description,
                      goal=scene.goal, source=USER, actions=patched,
                      aliases=scene.aliases or self._aliases_for(scene.name))
            saved = await self.store.save(s)
            return AgentResult(
                speech=f"改好了：{saved.name}现在是{self._preview_speech(saved.actions)}。",
                ui_card=self._scene_card("created", saved.name, saved.description,
                                         saved.actions))

        # 动作级改动 → 走同一条编译+校验+回读闭环（在原动作基础上重编）
        base = "、".join(action_desc(a) for a in scene.actions)
        draft = await compile_scene(
            self.llm, self.catalog, f"原有动作：{base}。修改要求：{mod}",
            name_hint=scene.name, model=os.getenv("LLM_MODEL_SCENE", ""))
        if not draft.ok:
            return AgentResult(speech=draft.error)
        await self._save_kv(ctx, SCENE_PENDING,
                            {"name": scene.name, "draft": draft.to_dict(), "overwrite": True})
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"改完是这样：{self._preview_speech(draft.actions)}。"
                   f"{self._dropped_speech(draft)}确认改吗？",
            follow_up="说「确认」即可",
            ui_card=self._scene_card("confirm", scene.name, draft.description, draft.actions))

    def _apply_param_override(self, actions: list, text: str) -> tuple[list, bool]:
        """从原话抠数值覆盖已有动作的参数（确定性，不 LLM 兜底；只改不增）。

        「温度改成24」→ hvac.temperature=24；「氛围灯调到 30%」→ ambient_light.brightness=30；
        「音量 20」→ volume.level=20；「座椅 170 度」→ seat.angle=170。解析不出就原样返回。
        """
        text = text or ""
        changed = False
        for pat, obj, key in (
            (r"(?:温度|空调|度数).{0,4}?(\d{1,2})\s*度?", "aircon", "temperature"),
            (r"(?:氛围灯|灯光|亮度).{0,4}?(\d{1,3})\s*[%％]?", "ambient_light", "brightness"),
            (r"(?:音量|声音).{0,4}?(\d{1,3})", "volume", "level"),
            (r"(?:座椅|靠背).{0,4}?(\d{2,3})\s*度?", "seat", "angle"),
        ):
            m = re.search(pat, text)
            if not m:
                continue
            for a in actions:
                r = resolve_command(str(a.get("command") or ""))
                if not r or r[0] != obj:
                    continue
                probe = {"type": "vehicle.control", "command": a["command"],
                         "params": {**(a.get("params") or {}), key: m.group(1)}}
                ok, cleaned, _ = validate_action(probe, self.catalog)
                if ok:
                    a["params"] = cleaned["params"]
                    changed = True
        return actions, changed

    async def _delete(self, intent, ctx, meta) -> AgentResult:
        query = (intent.slots.get("scene") or "").strip() or (intent.raw_text or "")
        scene = await self._match(ctx, query)
        if not scene:
            return AgentResult(speech="没找到这个场景。说「有哪些场景」我给你列一下。")
        confirmed = str(meta.get("confirmed", "")).lower() == "true"
        if not confirmed:
            what = "从列表里隐藏" if scene.source == BUILTIN else "删掉"
            return AgentResult(status=NEED_CONFIRM,
                               speech=f"确定要{what}{scene.name}吗？",
                               follow_up="说「确认」即可")
        if scene.source == BUILTIN:
            # 预置场景不能真删（随镜像发布）→ 存一条 disabled 的同名用户场景遮蔽它
            await self.store.save(Scene(
                user_id=self._uid(ctx), name=scene.name, description=scene.description,
                source=BUILTIN, status=DISABLED, actions=list(scene.actions)))
            return AgentResult(speech=f"好的，{scene.name}不再出现在场景列表里了。")
        await self.store.delete(self._uid(ctx), scene.id)
        return AgentResult(speech=f"已删除{scene.name}。")

    # ── scene.list ──────────────────────────────────────────────────────────
    async def _list_scenes(self, intent, ctx, meta) -> AgentResult:
        mine, builtin = await self._all_scenes(ctx)
        if not mine and not builtin:
            return AgentResult(speech="现在还没有场景。说「创建钓鱼模式：座椅放平、"
                                      "空调22度」就能造一个。")
        parts = []
        if mine:
            parts.append("你建的：" + "、".join(s.name for s in mine))
        if builtin:
            parts.append("内置的：" + "、".join(s.name for s in builtin))
        speech = "；".join(parts) + "。说「开启」加名字就能用。"
        if not mine:
            speech += "想要自己的场景就说「创建钓鱼模式：座椅放平、氛围灯调暗」。"
        return AgentResult(
            speech=speech,
            ui_card={"type": "scene_list", "display_priority": 1,
                     "mine": [s.to_card_item() for s in mine],
                     "builtin": [s.to_card_item() for s in builtin]},
            data={"scenes": [s.name for s in mine + builtin]})

    # ── 话术 / 卡片 ─────────────────────────────────────────────────────────
    @staticmethod
    def _preview_speech(actions: list, limit: int = 4) -> str:
        head = "、".join(action_desc(a) for a in actions[:limit])
        return head + ("等" if len(actions) > limit else "")

    @staticmethod
    def _dropped_speech(draft: Draft) -> str:
        if not draft.dropped:
            return ""
        return "；".join(dict.fromkeys(draft.dropped)) + "，已跳过。"

    @staticmethod
    def _scene_card(context: str, name: str, description: str, actions: list) -> dict:
        return {"type": "scene_card", "display_priority": 1, "context": context,
                "name": name, "description": description,
                "actions_preview": actions_preview(actions)}

    # ── shared_state（conventions §9）────────────────────────────────────────
    async def _save_kv(self, ctx, key: str, value: dict) -> None:
        try:
            await ctx.save_shared_state(key, value)
        except Exception as e:
            logger.warning("scene: 写 %s 失败（忽略）：%s", key, e)

    async def _load_kv(self, ctx, key: str) -> dict:
        try:
            data = await ctx.load_shared_state(key)
        except Exception:
            return {}
        try:
            d = json.loads(data) if isinstance(data, str) else (data or {})
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
