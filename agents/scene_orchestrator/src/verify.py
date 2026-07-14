"""Verify-Repair：执行后验证与修复闭环（D10）。

**动机（现状真缺陷）**：动作下发后 handle 已经返回，Agent 对"到底做成了没有"一无所知。
场景 5 个动作、其中一条被 VAL 安全门控拒绝（如低电量禁氛围灯），旧实现只会播最后一条动作的
成功话术——**失败对用户完全静默**。场景越丰富，这个缺陷越严重。

（分发器那一层已修根：拒绝不再被后续成功掩埋，见 `orchestrator/edge/server.py`。本模块管的是
**另一半**：VAL 说"执行成功"、状态却没落地的静默失败。2026-07-14 真栈首跑就抓到一个——
`ambient_light.set` 同时带 color+brightness 时，VAL 设色分支提前 return，亮度被静默丢弃，
四个预置场景的氛围灯亮度从来没生效过。这正是本模块存在的价值。）

做法：激活后起一个后台 task，等几秒让动作落地 + 状态 diff 经 NATS 回到镜像，再拿**编译期
就声明好的期望态**（或由动作确定性派生，见 `catalog.derive_assert`）逐条对账，未达成项按
声明的 `on_fail` 分类处置。

**三条铁律**：
1. **Repair 不新增执行通道**。proactive 只有 speech+card，所有「重试/补做」都经 `send_text`
   回到正常语音链路（权限/确认/VAL 门控全量重走）。执行入口唯一性是安全架构的正确性，
   不是限制。
2. **全程 fail-open**。拿不到状态、镜像空、异常——一律静默放弃。Verify 是增强，
   **绝不假警**、绝不因旁路失败影响主链。
3. **代际护栏 + 单飞**（v2.1 修正③）。SCENE_ACTIVE 是单槽，而 Verify 是几秒后的异步任务：
   旧 task 醒来时场景可能已被新激活覆盖 / 已退出。故 ① 对账清单经**闭包**携带（不回读单槽）
   ② 醒来先比对 `activation_id`，不一致直接放弃 ③ 新激活/退出先 cancel 旧 task。
"""
from __future__ import annotations

import asyncio
import logging
import time

from .catalog import derive_assert
from .compiler import action_desc
from .solve import unmet

logger = logging.getLogger("agent.scene.verify")

DEFAULT_WAIT_S = 4.0


class VerifyManager:
    """按 user_id 单飞的后台对账任务集。"""

    def __init__(self, mirror, publish, load_active, save_active,
                 wait_s: float = DEFAULT_WAIT_S):
        self._mirror = mirror
        self._publish = publish            # async (payload: dict) -> None
        self._load_active = load_active    # async (ctx_ids) -> dict
        self._save_active = save_active    # async (ctx_ids, dict) -> None
        self._wait_s = wait_s
        self._tasks: dict[str, asyncio.Task] = {}

    def cancel(self, user_id: str) -> None:
        """退出场景 / 新激活时先 cancel 旧 task（与代际校验双保险，覆盖 cancel 竞窗）。"""
        t = self._tasks.pop(user_id, None)
        if t and not t.done():
            t.cancel()

    def schedule(self, ctx_ids: tuple, scene_name: str, activation_id: str,
                 solved_actions: list) -> None:
        """注册对账任务。只对**声明/可派生期望态**的动作有意义，一条都没有就不起 task。"""
        checkable = [a for a in solved_actions
                     if (a or {}).get("assert") or derive_assert(a or {})]
        if not checkable:
            return
        user_id = ctx_ids[1]
        self.cancel(user_id)
        task = asyncio.create_task(
            self._run(ctx_ids, scene_name, activation_id, list(checkable)))
        self._tasks[user_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(user_id, None)
                               if self._tasks.get(user_id) is t else None)

    async def _run(self, ctx_ids: tuple, scene_name: str, activation_id: str,
                   checkable: list) -> None:
        try:
            await asyncio.sleep(self._wait_s)      # 等动作到端 + VAL 执行 + state diff 广播回来

            active = await self._load_active(ctx_ids)
            if (active or {}).get("activation_id") != activation_id:
                return                             # 被新激活覆盖 / 已退出 → 静默放弃（防错账假警）

            env = self._mirror.snapshot()
            if not env:
                return                             # 镜像没数据 = 无法验证（≠ 失败）→ 静默

            bad = unmet(checkable, env)            # 只算**确凿**未达成（读不到的不算）
            if not bad:
                return                             # 全达成 → 不打扰（HMI 已有执行反馈）

            await self._report(ctx_ids, scene_name, activation_id, bad, active)
        except asyncio.CancelledError:
            raise
        except Exception as e:                     # fail-open 铁律：旁路异常绝不影响主链
            logger.warning("scene verify 异常（忽略）：%s", e)

    async def _report(self, ctx_ids, scene_name: str, activation_id: str,
                      bad: list, active: dict) -> None:
        """未达成项按 on_fail 分类处置。每次激活**至多一条**汇报，不逐条轰炸。"""
        skip, retry, defer = [], [], []
        for a in bad:
            {"retry_suggest": retry, "defer_p": defer}.get(
                str(a.get("on_fail") or "skip"), skip).append(a)

        what = "、".join(action_desc(a) for a in bad[:3])
        buttons = []
        if retry:
            buttons.append({"label": "再试一次", "send_text": f"开启{scene_name}"})
        if defer:
            # 驻车补做队列（P3 消费：gear→P 变沿时发建议卡）。写前**再校验代际**——
            # 等待期间用户可能已经退出/换了场景，写进去就是脏数据。
            fresh = await self._load_active(ctx_ids)
            if (fresh or {}).get("activation_id") == activation_id:
                fresh["deferred"] = (fresh.get("deferred") or []) + [
                    {"command": a.get("command"), "params": a.get("params") or {},
                     "type": a.get("type") or "vehicle.control",
                     "reason": action_desc(a), "ts": int(time.time())}
                    for a in defer]
                await self._save_active(ctx_ids, fresh)

        tail = ("，停好车我再提醒你补上" if defer
                else "，要我再试一次吗" if retry else "")
        # 不猜原因：拒绝可能来自行车限制、低电量门控、儿童锁……VAL 的具体理由已在即时话术里
        # 播过（分发器不再让后续成功掩埋它），这里只做"没生效"的事实汇报。
        speech = f"{scene_name}已开启，不过{what}没有生效（被安全限制拦下了）{tail}。"
        await self._publish({
            "type": "scene_verify",
            "speech": speech,
            "card": {"type": "scene_card", "context": "suggest", "name": scene_name,
                     "description": f"{len(bad)} 个动作没生效",
                     "actions_preview": [{"label": action_desc(a), "danger": True}
                                         for a in bad],
                     "buttons": buttons},
            "agent_id": "scene-orchestrator",
            "user_id": ctx_ids[1],
            "ts": int(time.time() * 1000),
        })
        logger.info("scene verify: %s 有 %d 个动作未生效（skip=%d retry=%d defer=%d）；"
                    "未达成明细=%s",
                    scene_name, len(bad), len(skip), len(retry), len(defer),
                    [{"cmd": a.get("command"),
                      "expect": a.get("assert") or derive_assert(a),
                      "actual": self._mirror.get(
                          (a.get("assert") or derive_assert(a) or {}).get("key"))}
                     for a in bad])
