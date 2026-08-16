# build context 为项目根。
FROM python:3.11-slim
WORKDIR /app

COPY proactive/requirements.txt /tmp/req.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked pip install -r /tmp/req.txt

COPY proactive /app/proactive
COPY observability /app/observability
# B3 的部署形态闸：`proactive/main.py` import `runtime.profile`。少这一行进程起不来
# ——而既有容器跑的是加闸之前的镜像，**只有重建才暴露**（2026-08-11 真栈演练抓到）。
COPY runtime /app/runtime
COPY scripts/e2e_identity.py /app/scripts/e2e_identity.py

ENV PYTHONPATH=/app
ENV PROACTIVE_HTTP_PORT=50075
CMD ["python", "-m", "proactive.main"]
