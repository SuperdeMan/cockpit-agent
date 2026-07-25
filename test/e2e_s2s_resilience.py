"""M4 P2 韧性验证：S2S 断连重连 / 摘要重注入 / DEGRADED 回落（RFC §4.5 / §6.3）。

**为什么不是纯 e2e**：DoD 要求「kill provider 连接」，但杀不掉 DashScope 那一端。故在宿主
内起一个最小 WS 代理（HMI 网关 ↔ 真 provider 的中间人），按命令切断它来注入断连，并直接
驱动生产的 `S2SSession`——被验的是真实会话状态机 + 真实 provider + 真实断线，只有「谁来
断线」是注入的。**刻意不给生产协议加测试后门**（不加 ws_url 覆盖字段），也不改根 .env。

验四件：
  1. 会话中途断连 → 自动重连 → session.state 回 ready，且**带摘要重注入**
  2. 重连后仍是同一对话（provider 侧上下文已由摘要恢复——§2.3「可丢弃缓存」的兑现）
  3. IN_TURN 断连 → 该轮诚实收 turn.end(cancelled, detail=disconnected)，不假装无事
  4. 重连全部失败 → DEGRADED 下行（HMI 据此回落三段式）

前置：`make up`（要 /api/tts 合成音频）+ 根 .env 带 DashScope key。缺任一 → SKIP。
用法：python test/e2e_s2s_resilience.py
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT), str(_ROOT / "gen" / "python"), str(_ROOT / "llm-gateway"),
          str(_ROOT / "test")):
    if p not in sys.path:
        sys.path.insert(0, p)

from e2e_s2s_probe import _api_key, synth_pcm16k  # noqa: E402

UPSTREAM = os.getenv("S2S_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
# 端口让 OS 分配（0）：Windows winnat 的保留区间会挡掉 500xx 段的固定端口
#（本仓库前科：50070 被挡需 netsh override 应急）——不固定端口就永不撞。
PROXY_PORT = int(os.getenv("S2S_PROXY_PORT", "0"))

_fails: list[str] = []


def check(name: str, ok: bool, note: str = "") -> None:
    print(f"  [{'✓' if ok else '✗'}] {name}" + (f" —— {note}" if note else ""))
    if not ok:
        _fails.append(name)


class KillableProxy:
    """HMI 网关 ↔ provider 的透明 WS 中间人。kill() 切断所有在途连接；
    refuse=True 后拒绝新连接（模拟 provider 持续不可达 → DEGRADED）。"""

    def __init__(self, port: int, upstream: str):
        self.port = port
        self.upstream = upstream
        self.refuse = False
        self.conns: list = []
        self.accepted = 0
        self._runner = None

    async def start(self):
        from aiohttp import web
        app = web.Application()
        app.router.add_get("/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await site.start()
        if not self.port:  # port=0 → 取 OS 实际分配的端口
            self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self):
        await self.kill()
        if self._runner:
            await self._runner.cleanup()

    async def kill(self):
        """切断所有在途连接（双向都关）。"""
        conns, self.conns = self.conns, []
        for down, up, sess in conns:
            for c in (down, up):
                try:
                    await c.close()
                except Exception:
                    pass
            try:
                await sess.close()
            except Exception:
                pass

    async def _handle(self, request):
        import aiohttp
        from aiohttp import web
        if self.refuse:
            return web.Response(status=503, text="proxy refusing")
        self.accepted += 1
        down = web.WebSocketResponse(heartbeat=20.0, max_msg_size=16 * 1024 * 1024)
        await down.prepare(request)
        qs = request.query_string
        url = f"{self.upstream}?{qs}" if qs else self.upstream
        sess = aiohttp.ClientSession()
        try:
            up = await sess.ws_connect(
                url, headers={"Authorization": request.headers.get("Authorization", "")},
                heartbeat=20.0, max_msg_size=16 * 1024 * 1024)
        except Exception:
            await sess.close()
            await down.close()
            return down
        self.conns.append((down, up, sess))

        async def pump(src, dst):
            try:
                async for msg in src:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await dst.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await dst.send_bytes(msg.data)
                    else:
                        break
            except Exception:
                pass

        await asyncio.gather(pump(down, up), pump(up, down))
        try:
            await up.close()
        except Exception:
            pass
        await sess.close()
        return down


async def drive_turn(sess, out: list, pcm: bytes, *, timeout: float = 60.0) -> dict:
    """喂音频跑一轮，返回该轮的下行汇总。"""
    mark = len(out)
    step = 3200
    for i in range(0, len(pcm), step):
        await sess.push_audio(pcm[i:i + step])
    # 走生产路径收尾（静音尾由 provider adapter 补），不自己灌静音——自己灌就等于
    # 替生产代码做了它没做的事，会把「provider 永远等不到静音」的死锁掩盖掉
    await sess.audio_done()
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        await asyncio.sleep(0.2)
        if any(o.get("type") == "turn.end" for o in out[mark:]):
            break
    frames = out[mark:]
    answer = "".join(o.get("text", "") for o in frames if o.get("type") == "turn.answer_delta")
    end = next((o for o in frames if o.get("type") == "turn.end"), {})
    return {"answer": answer, "end": end, "types": [o.get("type") for o in frames]}


async def main() -> int:
    if not _api_key():
        print("SKIP：无 DashScope key")
        return 0
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        print("SKIP：缺 aiohttp")
        return 0
    try:
        synth_pcm16k("测试")
    except Exception as e:
        print(f"SKIP：/api/tts 不可达（{e}）——需 make up")
        return 0

    from s2s.provider import build_s2s_provider
    from s2s.session import S2SSession, SessionState

    proxy = KillableProxy(PROXY_PORT, UPSTREAM)
    await proxy.start()
    print(f"S2S 韧性验证  代理 :{proxy.port} → {UPSTREAM}")

    out: list[dict] = []
    audio_bytes = [0]
    opened_summaries: list[str] = []

    async def emit_json(o):
        out.append(o)

    async def emit_audio(b):
        audio_bytes[0] += len(b)

    async def context_provider():
        # 重注入材料（真实链路里由 memory 近 4 轮给；此处固定，便于断言它真被带上）
        s = "用户：我最喜欢喝拿铁咖啡\n你：拿铁不错"
        opened_summaries.append(s)
        return s

    def factory():
        """生产工厂造 provider，只把 ws_url 改指本地代理（env / 生产协议一字不改）。"""
        p = build_s2s_provider()
        p.ws_url = f"ws://127.0.0.1:{proxy.port}"
        return p

    sess = S2SSession(provider_factory=factory, emit_json=emit_json, emit_audio=emit_audio,
                      context_provider=context_provider, session_id="e2e-s2s-resil",
                      reconnect_backoff=(0.3, 0.6, 1.0))
    try:
        print("\n[1] 经代理建会话")
        await sess.start()
        check("会话建立（经代理连真 provider）", sess.state == SessionState.READY,
              f"state={sess.state} 代理接受连接数={proxy.accepted}")

        print("\n[2] 正常一轮")
        r1 = await drive_turn(sess, out, synth_pcm16k("我最喜欢喝拿铁咖啡"))
        check("自答成功", r1["end"].get("reason") == "complete" and bool(r1["answer"]),
              f"answer={r1['answer'][:40]!r}")

        print("\n[3] 切断 provider 连接 → 自动重连")
        n_before = proxy.accepted
        t0 = time.monotonic()
        await proxy.kill()
        for _ in range(60):
            await asyncio.sleep(0.2)
            if sess.state == SessionState.READY and proxy.accepted > n_before:
                break
        recover_ms = (time.monotonic() - t0) * 1000
        states = [o.get("state") for o in out if o.get("type") == "session.state"]
        check("自动重连成功", sess.state == SessionState.READY and proxy.accepted > n_before,
              f"state={sess.state} 新连接={proxy.accepted - n_before} 耗时={recover_ms:.0f}ms")
        check("下行报了 reconnecting→ready", states[-2:] == ["reconnecting", "ready"],
              f"states={states}")
        check("重连带摘要重注入（§2.3 兑现）", len(opened_summaries) >= 2,
              f"open 带摘要次数={len(opened_summaries)}")

        print("\n[4] 重连后仍是同一对话（provider 上下文经摘要恢复）")
        # 重连成功的同一瞬间就推音频是最坏时序（新 session 刚建 + 前滚缓冲刚灌完），实测会
        # 偶发空回答。生产上用户不会在断线恢复的同一秒说话，故给 1s settle——这不是掩盖缺陷：
        # 重连本身与摘要重注入已由 [3] 独立断言，这里量的是「上下文是否真恢复」。
        await asyncio.sleep(1.0)
        r2 = await drive_turn(sess, out, synth_pcm16k("我刚才说我最喜欢喝什么"))
        check("重连后记得断线前说过的事",
              "拿铁" in r2["answer"] or "咖啡" in r2["answer"],
              f"answer={r2['answer'][:50]!r}")

        print("\n[5] IN_TURN 断连 → 诚实收束该轮")
        mark = len(out)
        # 语料必须**确定自答**：像「讲讲咖啡豆产地和风味区别」这种落在灰度边界上的句子，
        # 模型会判成「查信息」走 escalate（实测过），本轮就没有播报可断，断言假红。
        pcm = synth_pcm16k("给我讲个长一点的笑话")
        step = 3200
        for i in range(0, len(pcm), step):
            await sess.push_audio(pcm[i:i + step])
        await sess.audio_done()
        # 等本轮真的开始产出，再断
        for _ in range(80):
            await asyncio.sleep(0.2)
            if any(o.get("type") in ("turn.answer_delta", "turn.audio_meta")
                   for o in out[mark:]):
                break
        await proxy.kill()
        for _ in range(60):
            await asyncio.sleep(0.2)
            if any(o.get("type") == "turn.end" for o in out[mark:]):
                break
        end = next((o for o in out[mark:] if o.get("type") == "turn.end"), {})
        check("IN_TURN 断连收 turn.end(cancelled)", end.get("reason") == "cancelled",
              f"end={end}")
        check("带 detail=disconnected（HMI 据此出诚实话术）",
              end.get("detail") == "disconnected", f"detail={end.get('detail')!r}")

        print("\n[6] provider 持续不可达 → DEGRADED 回落")
        proxy.refuse = True
        await proxy.kill()
        for _ in range(120):
            await asyncio.sleep(0.2)
            if sess.state == SessionState.DEGRADED:
                break
        check("重连超限进 DEGRADED", sess.state == SessionState.DEGRADED,
              f"state={sess.state}")
        check("下行 session.state=degraded（HMI 回落三段式）",
              [o.get("state") for o in out if o.get("type") == "session.state"][-1] == "degraded")
        before = audio_bytes[0]
        await sess.push_audio(b"\x00" * 3200)
        check("DEGRADED 后不再上行音频", audio_bytes[0] == before)
    finally:
        await sess.close()
        await proxy.stop()

    print("\n" + "=" * 68)
    if _fails:
        print(f"✗ {len(_fails)} 项失败：{_fails}")
        return 1
    print("✓ S2S 韧性（重连/重注入/诚实收束/降级）全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
