FROM python:3.11-slim

WORKDIR /app
COPY observability/collector/requirements.txt ./req.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked pip install -r req.txt
COPY observability/ ./observability/
# B3 的部署形态闸：`observability/collector/main.py` import `runtime.profile`。少这一行
# 进程起不来——而既有容器跑的是加闸之前的镜像，**只有重建才暴露**（2026-08-11 真栈演练抓到）。
COPY runtime/ ./runtime/

ENV PYTHONPATH=/app
EXPOSE 8092

CMD ["python", "-m", "observability.collector.main"]
