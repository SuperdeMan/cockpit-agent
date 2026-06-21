"""场景编排 Agent（scene-orchestrator）—— Leaf 工具型。

把「回家模式/午休模式/露营模式」等命名场景展开为一组确定性动作。
与 Planner 的边界：Planner 擅长临时多意图；scene-orchestrator 管预定义命名场景。
"""
from __future__ import annotations
import logging
import os
import yaml
from difflib import get_close_matches

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED, NEED_CONFIRM

logger = logging.getLogger("agent.scene_orchestrator")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")
_SCENES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scenes.yaml")


def _load_scenes(path: str) -> dict:
    """加载场景知识库。"""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("scenes", {})


class SceneOrchestratorAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self._scenes = _load_scenes(_SCENES_PATH)

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "scene.activate": self._activate,
            "scene.deactivate": self._deactivate,
            "scene.list": self._list_scenes,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="场景助手暂不支持该请求。")

    def _match_scene(self, query: str) -> tuple[str, dict] | None:
        """模糊匹配场景名。"""
        query = query.strip().lower()
        if not query:
            return None

        # 精确匹配 key
        if query in self._scenes:
            return query, self._scenes[query]

        # 精确匹配 name
        for key, scene in self._scenes.items():
            if scene.get("name", "").lower() == query:
                return key, scene

        # 别名匹配
        for key, scene in self._scenes.items():
            aliases = scene.get("aliases", [])
            if query in [a.lower() for a in aliases]:
                return key, scene

        # 模糊匹配
        all_names = [s.get("name", "") for s in self._scenes.values()]
        matches = get_close_matches(query, all_names, n=1, cutoff=0.6)
        if matches:
            for key, scene in self._scenes.items():
                if scene.get("name") == matches[0]:
                    return key, scene

        return None

    async def _activate(self, intent, ctx, meta) -> AgentResult:
        """激活场景模式。"""
        scene_key = intent.slots.get("scene", "").strip()
        if not scene_key:
            return AgentResult(
                status=NEED_SLOT, speech="您想开启哪个场景？",
                follow_up="可以说『回家模式』『露营模式』等",
                missing_slots=["scene"])

        match = self._match_scene(scene_key)
        if not match:
            available = "、".join(s["name"] for s in self._scenes.values())
            return AgentResult(
                speech=f"没有找到「{scene_key}」场景。可用场景：{available}")

        key, scene = match

        # 展开场景动作
        actions = []
        needs_confirm = False
        for a in scene.get("actions", []):
            actions.append(self._build_action(a))
            if a.get("require_confirm"):
                needs_confirm = True

        if needs_confirm:
            # 有危险动作 → NEED_CONFIRM
            confirm_actions = [a for a in actions if a["require_confirm"]]
            desc = "、".join(self._action_desc(a) for a in confirm_actions)
            return AgentResult(
                status=NEED_CONFIRM,
                speech=f"即将开启{scene['name']}。其中{desc}需要您确认。确认执行吗？",
                follow_up="说『确认』即可",
            ).action("scene.activate", {"scene": key, "actions": actions},
                     require_confirm=True)

        # 无危险动作 → 直接执行
        result = AgentResult(speech=f"已为您开启{scene['name']}。")
        for a in actions:
            result.action(a["type"], a["payload"])
        return result

    async def _deactivate(self, intent, ctx, meta) -> AgentResult:
        """退出场景模式。"""
        scene_key = intent.slots.get("scene", "").strip()
        if not scene_key:
            return AgentResult(speech="已退出当前场景模式。")
        match = self._match_scene(scene_key)
        if match:
            return AgentResult(speech=f"已退出{match[1]['name']}。")
        return AgentResult(speech=f"当前没有激活「{scene_key}」场景。")

    async def _list_scenes(self, intent, ctx, meta) -> AgentResult:
        """列出可用场景。"""
        if not self._scenes:
            return AgentResult(speech="暂无可用场景。")
        names = "、".join(s["name"] for s in self._scenes.values())
        return AgentResult(
            speech=f"可用场景：{names}。说『开启』加场景名即可激活。",
            data={"scenes": list(self._scenes.keys())},
        )

    @staticmethod
    def _build_action(a: dict) -> dict:
        """把场景定义里的一条动作转成可下发的 action。

        vehicle.control 的指令名（scenes.yaml 的 command，如 hvac.set）必须并入 payload——
        VAL 经 payload["command"] 取指令（见 orchestrator/edge/server.py），丢掉它动作即不可
        执行；且空 payload 的 vehicle.control（如 fragrance.on params 为空）会被 Executor 的
        action 校验直接丢弃。navigate 等无 command 的动作沿用 payload 不变。
        """
        payload = dict(a.get("params") or a.get("payload") or {})
        if a.get("command"):
            payload = {"command": a["command"], **payload}
        return {
            "type": a["type"],
            "payload": payload,
            "require_confirm": a.get("require_confirm", False),
        }

    @staticmethod
    def _action_desc(action: dict) -> str:
        """生成动作的人类可读描述。"""
        action_type = action.get("type", "")
        if action_type == "vehicle.control":
            cmd = action.get("payload", {}).get("command", "")
            return f"车辆控制（{cmd}）"
        if action_type == "navigate":
            return "导航"
        return action_type
