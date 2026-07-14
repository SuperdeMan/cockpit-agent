"""触发运行时（D6/D7）：时间 + 事件双 watcher，产物**只有建议卡**。

**触发路径零执行权**（D6，本模块的安全底线）：触发命中只发 `agent.proactive` 建议卡
（「检测到电量低于20%，要开启省电出行模式吗？」+ 按钮），**执行永远经用户显式指令走正常
语音链路**（权限/确认/VAL 门控全不绕过）。自动化规则在行车环境直接动车身是量产不可接受的
安全面——即便 VAL 有门控，也不该让触发器成为第二条执行入口。

两个 watcher 都在 Agent 进程内自治（D7）：
- **时间**：poll（默认 30s；场景触发不需要 reminder 的 5s 精度）→ 枚举 enabled 场景的到期
  time trigger → 发建议卡 → 按 recur 滚动到下一次。
- **事件**：挂 `StateMirror.on_change`（**不新建订阅**，§7.2「一条订阅多消费方」）→
  **边沿触发**：只在「从不满足 → 满足」的变沿发一次，防 battery=19 持续风暴 → 同场景节流。
- **驻车补做**（第三消费方）：`SCENE_ACTIVE.deferred[]` 非空且 gear→P 变沿 → 发补做建议卡。

单实例边界（诚实留档）：触发去重是**进程内**的（`_fired`/`_edge`）。多实例部署需要把
last_fired 落库做原子领取（reminder `claim_due` 那套）——PoC 单实例不做，写在这里备查。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from .compiler import actions_preview
from .solve import SAT, evaluate

logger = logging.getLogger("agent.scene.triggers")

# 业务时区（中国车机 PoC 固定 UTC+8，与 reminder 的 Asia/Shanghai 默认一致）。
# time watcher 与策略求值的 `hour` 环境键（agent._ground）都用它——容器本地时是 UTC，
# 直接 time.localtime() 会让「晚上10点后」这类条件错 8 小时。
BUSINESS_TZ = timezone(timedelta(hours=8))

_OP_ALIASES = {"enter": "eq", "leave": "ne"}       # 位置进入/离开 → 边沿语义已由 watcher 保证
RECURS = ("daily", "workday", "once")
_SCENES_CACHE_S = 10.0     # enabled 场景短缓存：车速这类高频状态广播不该次次打 DB


def enrich_env(state: dict) -> dict:
    """把镜像里的复合值摊平成条件可引用的 key（location 是 dict → location.city）。"""
    env = dict(state or {})
    loc = env.get("location")
    if isinstance(loc, dict):
        for k in ("city", "district", "name"):
            if loc.get(k):
                env[f"location.{k}"] = loc[k]
    return env


def _cond(spec: dict) -> dict:
    return {"key": spec.get("key"), "value": spec.get("value"),
            "op": _OP_ALIASES.get(str(spec.get("op") or "eq"), str(spec.get("op") or "eq"))}


def next_fire_at(spec: dict, now: datetime, tz) -> int:
    """time trigger 的下一次触发时刻（epoch 秒）。解析不出 → 0。"""
    at = str(spec.get("at") or "").strip()
    try:
        hh, mm = (int(x) for x in at.split(":", 1))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):   # 越界时刻 → 不触发（别拿脏数据去 replace）
            return 0
    except (ValueError, AttributeError):
        return 0
    local = now.astimezone(tz)
    fire = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    recur = str(spec.get("recur") or "daily")
    for _ in range(8):
        if fire > local and (recur != "workday" or fire.weekday() < 5):
            return int(fire.astimezone(timezone.utc).timestamp())
        fire += timedelta(days=1)
    return 0


class TriggerWatcher:
    def __init__(self, store, mirror, publish, *, poll_s: float = 30.0,
                 throttle_s: float = 1800.0, tz=None,
                 load_active=None, users=("u1",)):
        self._store = store
        self._mirror = mirror
        self._publish = publish
        self._poll_s = poll_s
        self._throttle_s = throttle_s
        self._tz = tz or BUSINESS_TZ
        self._load_active = load_active          # async (ctx_ids) -> SCENE_ACTIVE
        self._users = list(users)                # PoC 单用户；多用户需 store 层枚举
        self._fired: dict[str, float] = {}       # 节流：scene_id|idx -> 上次触发
        self._edge: dict[str, bool] = {}         # 边沿：scene_id|idx -> 上次是否满足
        self._due: dict[str, int] = {}           # 时间触发的下一次时刻（消费后重算=recur 滚动）
        self._scenes_at = 0.0                    # _enabled_scenes 缓存时间戳
        self._scenes_cached: list = []
        self._task: asyncio.Task | None = None
        self._parked = True                      # gear 边沿（驻车补做用）

    async def start(self) -> None:
        self._mirror.on_change(self._on_state)   # 事件触发挂已有订阅，不新建
        self._task = asyncio.create_task(self._poll_forever())
        logger.info("scene triggers: 时间 poll=%ss，事件挂车况镜像", self._poll_s)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    # ── 事件触发（边沿 + 节流）───────────────────────────────────────────────
    async def _on_state(self, changes: list, state: dict) -> None:
        env = enrich_env(state)
        for scene in await self._enabled_scenes():
            for i, t in enumerate(scene.triggers or []):
                if str((t or {}).get("type")) != "event":
                    continue
                key = f"{scene.id}|{i}"
                ok = evaluate(_cond(t.get("spec") or {}), env) == SAT
                was = self._edge.get(key, False)
                self._edge[key] = ok
                # **边沿触发**：只在「从不满足 → 满足」发一次。否则 battery=19 每来一次
                # 状态广播就播一遍，成了骚扰风暴。
                if ok and not was and self._allow(key):
                    await self._suggest(scene, self._reason(t, env))

        await self._check_deferred(env)          # 第三消费方：驻车补做

    async def _check_deferred(self, env: dict) -> None:
        """gear→P 变沿 + deferred 非空 → 发补做建议卡（P2 verify 挂的队列在这里兑现）。"""
        parked = str(env.get("gear") or "").upper() == "P"
        was, self._parked = self._parked, parked
        if not parked or was or not self._load_active:
            return
        for uid in self._users:
            try:
                active = await self._load_active(("", uid, "")) or {}
            except Exception:
                continue
            deferred = active.get("deferred") or []
            name = active.get("scene_name") or "场景"
            if not deferred or not self._allow(f"deferred|{uid}", 300):
                continue
            what = "、".join(d.get("reason") or d.get("command", "") for d in deferred[:3])
            await self._publish({
                "type": "scene_suggest", "agent_id": "scene-orchestrator", "user_id": uid,
                "ts": int(time.time() * 1000),
                "speech": f"已经停好车了，刚才{name}里没做成的{what}，现在补上吗？",
                "card": {"type": "scene_card", "context": "suggest", "name": name,
                         "description": "停车后可以补做",
                         "actions_preview": [{"label": d.get("reason") or d.get("command", ""),
                                              "danger": True} for d in deferred],
                         "buttons": [{"label": "补上", "send_text": f"开启{name}"},
                                     {"label": "不用", "send_text": ""}]},
            })

    # ── 时间触发（poll）─────────────────────────────────────────────────────
    async def _poll_forever(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._poll_s)
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:                 # 触发是旁路，异常绝不拖垮 Agent
                logger.warning("scene triggers poll 异常（忽略）：%s", e)

    async def poll_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        now_ts = int(now.timestamp())
        fired = 0
        for scene in await self._enabled_scenes():
            for i, t in enumerate(scene.triggers or []):
                if str((t or {}).get("type")) != "time":
                    continue
                key = f"{scene.id}|{i}"
                spec = t.get("spec") or {}
                due = self._due_at(key, spec, now)
                if due and now_ts >= due and self._allow(key, 3600):
                    await self._suggest(scene, f"到{spec.get('at')}了")
                    if str(spec.get("recur") or "daily") == "once":
                        # once：消费即熄（置 0 哨兵，_due_at 不再重算）。进程重启会重新装填、
                        # 在未来同一时刻再触发一次——单实例进程内去重的诚实边界（模块头注释）。
                        self._due[key] = 0
                    else:
                        self._due.pop(key, None)   # 消费后重算下一次（recur 滚动）
                    fired += 1
        return fired

    def _due_at(self, key: str, spec: dict, now: datetime) -> int:
        if key not in self._due:
            self._due[key] = next_fire_at(spec, now, self._tz)
        return self._due[key]

    # ── 共用 ────────────────────────────────────────────────────────────────
    async def _enabled_scenes(self) -> list:
        """带短缓存（10s）：事件路径每条 `vehicle.state.changed` 都会进来，而行车中车速
        广播几乎连续——不缓存就是一场 DB 查询风暴。触发本身有分钟级节流，缓存不损语义
        （新建触发器最晚 10s 后被看见）。"""
        now = time.time()
        if now - self._scenes_at < _SCENES_CACHE_S:
            return self._scenes_cached
        out = []
        for uid in self._users:
            try:
                out.extend(s for s in await self._store.list(uid) if s.triggers)
            except Exception as e:
                logger.debug("scene triggers: 读场景失败：%s", e)
        self._scenes_at, self._scenes_cached = now, out
        return out

    def _allow(self, key: str, throttle_s: float | None = None) -> bool:
        now = time.time()
        if now - self._fired.get(key, 0) < (throttle_s or self._throttle_s):
            return False
        self._fired[key] = now
        return True

    @staticmethod
    def _reason(t: dict, env: dict) -> str:
        spec = t.get("spec") or {}
        k = spec.get("key")
        labels = {"battery": "电量", "gear": "挡位", "speed_kmh": "车速",
                  "cabin_temp": "车内温度", "location.city": "位置"}
        return f"{labels.get(k, k)}现在是 {env.get(k)}"

    async def _suggest(self, scene, reason: str) -> None:
        """**只发建议卡，零执行权**（D6）。用户点「开启」→ 回发原话走正常语音链路。"""
        await self._publish({
            "type": "scene_suggest", "agent_id": "scene-orchestrator",
            "user_id": scene.user_id or "u1", "ts": int(time.time() * 1000),
            "speech": f"{reason}，要开启{scene.name}吗？",
            "card": {"type": "scene_card", "context": "suggest", "name": scene.name,
                     "description": scene.description or reason,
                     "actions_preview": actions_preview(scene.actions or []),
                     "buttons": [{"label": "开启", "send_text": f"开启{scene.name}"},
                                 {"label": "不用", "send_text": ""}]},
        })
        logger.info("scene trigger: 建议开启 %s（%s）", scene.name, reason)
