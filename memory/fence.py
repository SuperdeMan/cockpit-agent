"""CA2-15 S1b：owner 代际——删除与在途写入同代际失效。

修前 Memory 没有栅栏：forget 落在一次抽取的「读轮次 → LLM → 写条目」之间，抽取结果在删除之后写回；
云端回合结束才写轮次、Agent 执行期间写记忆、车端与 S2S 回流，都可能把删除之前观测到的内容写回来。

**判据**：每个用户一个代际 ``<代数>.<随机 128 位>``（Redis ``mem_epoch:{user}``，无 TTL；Redis 不可用时
与会话轮次一样退回进程内存），首次读取惰性创建为代数 0。删除在删之前推进代际；写入方在读记忆之前取一次代际、
写入时原样带回，不等于当前值的写入被拒（``stale_memory_epoch``）。Redis 丢了这个键时重建出的随机部分
与在途写入带来的旧值不相等 ⇒ 拒绝——失败方向是少写，不是复活。

**串行点**：Memory 是记忆数据的唯一写入口，且是单副本。同一用户的「复核代际 + 写入」与「推进代际 + 删除」
在本进程内由一把按用户的锁互斥，复核与写入之间因此没有窗口，不需要跨存储事务或事后补偿。
⚠ Memory 扩成多副本之前，这把锁必须换成跨进程的。
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

logger = logging.getLogger("memory.fence")

KEY = "mem_epoch:{}"
STALE = "stale_memory_epoch"


class StaleEpoch(Exception):
    """写入方观测记忆之后，这个用户的记忆被删过。"""


def generation(epoch: str) -> int:
    head, dot, _ = (epoch or "").partition(".")
    return int(head) if dot and head.isdigit() else -1


def eligible(turn_epoch: str, current: str) -> bool:
    """这一轮能不能进入代际 ``current`` 的抽取窗口。

    盖了章的轮次只认同一代际；修前落库、没有代际字段的轮次只在这个用户从没删过（代数 0）时有效。"""
    if turn_epoch:
        return turn_epoch == current
    return generation(current) == 0


def _mint(gen: int) -> str:
    return f"{gen}.{secrets.token_hex(16)}"


class OwnerFence:
    def __init__(self, redis_getter):
        self._redis = redis_getter          # async () -> redis client | None（MemoryStore._redis）
        self._mem: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._holders: dict[str, int] = {}

    async def current(self, user_id: str) -> str:
        if not user_id:
            return ""
        r = await self._redis()
        if r is None:
            return self._mem.setdefault(user_id, _mint(0))
        key = KEY.format(user_id)
        value = await r.get(key)
        if not value:
            await r.set(key, _mint(0), nx=True)
            value = await r.get(key)
        return value

    async def _advance(self, user_id: str) -> str:
        nxt = _mint(generation(await self.current(user_id)) + 1)
        r = await self._redis()
        if r is None:
            self._mem[user_id] = nxt
        else:
            await r.set(KEY.format(user_id), nxt)
        return nxt

    @asynccontextmanager
    async def _owner(self, user_id: str):
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        self._holders[user_id] = self._holders.get(user_id, 0) + 1
        try:
            async with lock:
                yield
        finally:
            left = self._holders[user_id] - 1
            if left:
                self._holders[user_id] = left
            else:
                del self._holders[user_id]
                self._locks.pop(user_id, None)

    @asynccontextmanager
    async def writing(self, user_id: str, observed: str = ""):
        """持锁写入；写入方带来的代际不是当前值就拒绝。产出当前代际（轮次据此盖章）。

        ``observed`` 为空 = 旧调用方：照旧写，但仍与删除串行。"""
        if not user_id:
            yield ""
            return
        async with self._owner(user_id):
            current = await self.current(user_id)
            if observed and observed != current:
                raise StaleEpoch(STALE)
            yield current

    @asynccontextmanager
    async def deleting(self, user_id: str):
        """持锁先推进代际再删除：删除完成之前与之后，带旧代际的写入都进不来。"""
        if not user_id:
            yield ""
            return
        async with self._owner(user_id):
            yield await self._advance(user_id)
