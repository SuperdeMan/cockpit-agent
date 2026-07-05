"""极简出站正向代理（ws8 第三方 Agent 网络白名单）。

只支持 HTTP CONNECT（HTTPS 隧道）+ 域名白名单默认拒绝——第三方 Agent 经 HTTP_PROXY 出站，
只放行 EGRESS_ALLOW 里的可信厂商域名，其余 403。纯 Python stdlib（asyncio），无第三方依赖，
跑在已有的 python:3.11-slim 上，不引新镜像。

背景：原 envoy-proxy.yaml 是反向代理（把 / round-robin 混发多域、不支持 CONNECT），作
HTTP_PROXY 用时 httpx 的 CONNECT 全失败 → Agent 降级 mock。改用本代理后第三方 Agent 出站
重新受白名单约束（见 docs/design/2026-07-05-nearby-discovery-redesign.md §11）。

配置：EGRESS_ALLOW=逗号分隔的允许域名；EGRESS_PORT=监听端口（默认 8080）。
"""
from __future__ import annotations
import asyncio
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s egress %(levelname)s %(message)s")
log = logging.getLogger("egress")

ALLOW = {h.strip().lower() for h in os.getenv("EGRESS_ALLOW", "").split(",") if h.strip()}
PORT = int(os.getenv("EGRESS_PORT", "8080"))


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    peer = cw.get_extra_info("peername")
    try:
        line = await asyncio.wait_for(cr.readline(), timeout=15)
        if not line:
            cw.close()
            return
        parts = line.decode("latin1").split()
        # 只受理 CONNECT（HTTPS 隧道）；明文 HTTP 一律拒（当前 provider 均走 https）
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            cw.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            await cw.drain()
            cw.close()
            return
        hostport = parts[1]
        host, _, port_s = hostport.rpartition(":")
        host = (host or hostport).lower()
        port = int(port_s) if port_s.isdigit() else 443
        # 读掉剩余请求头直到空行
        while True:
            h = await asyncio.wait_for(cr.readline(), timeout=15)
            if h in (b"\r\n", b"\n", b""):
                break
        if host not in ALLOW:
            log.warning("DENY %s from %s", hostport, peer)
            cw.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await cw.drain()
            cw.close()
            return
        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10)
        except Exception as e:
            log.warning("UPSTREAM_FAIL %s: %s", hostport, e)
            cw.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await cw.drain()
            cw.close()
            return
        log.info("ALLOW %s", hostport)
        cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await cw.drain()
        await asyncio.gather(_pipe(cr, uw), _pipe(ur, cw))
    except Exception as e:
        log.warning("ERR %s: %s", peer, e)
        try:
            cw.close()
        except Exception:
            pass


async def main() -> None:
    log.info("egress forward-proxy on :%d  allowlist=%s", PORT, sorted(ALLOW))
    server = await asyncio.start_server(_handle, "0.0.0.0", PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
