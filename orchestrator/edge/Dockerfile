# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
WORKDIR /app/orchestrator/edge

COPY orchestrator/edge/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked pip install -r requirements.txt

COPY gen/python /app/gen/python
COPY orchestrator/edge /app/orchestrator/edge
# AR05 F07：端侧 T0 的授权闸复用 security.permission / security.session_scopes
# （与云侧同一份判据；纯标准库，无额外依赖）。缺这一行端侧启动即 ImportError。
COPY security /app/security
COPY observability /app/observability
COPY runtime /app/runtime

ENV PYTHONPATH=/app:/app/gen/python
ENV EDGE_ORCHESTRATOR_PORT=50070
CMD ["python", "main.py"]
