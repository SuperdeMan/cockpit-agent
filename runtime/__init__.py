"""共享运行时工具：全 Python 服务通用的进程级基础设施。

当前仅含 `grpcio`（统一 keepalive 拨号 / 建服务 / 优雅停机）。各服务镜像经
Dockerfile `COPY runtime /app/runtime` 引入，`/app` 在 PYTHONPATH 上即可
`from runtime.grpcio import aio_channel, aio_server, run_aio_server`。
"""
