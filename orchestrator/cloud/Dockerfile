# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
WORKDIR /app

COPY orchestrator/cloud/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gen/python /app/gen/python
COPY orchestrator/cloud /app/orchestrator/cloud
COPY security /app/security
COPY observability /app/observability
COPY runtime /app/runtime
COPY skills /app/skills

# /app 进 PYTHONPATH：orchestrator.cloud 包内相对 import + security 依赖都从这里解析
ENV PYTHONPATH=/app:/app/gen/python
ENV CLOUD_PLANNER_PORT=50054
CMD ["python", "-m", "orchestrator.cloud.main"]
