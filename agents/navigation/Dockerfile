# build context 为项目根（见 deploy/docker-compose.yaml）。需先 `make proto` 生成 gen/python。
FROM python:3.11-slim
WORKDIR /app

COPY agents/_sdk/requirements.txt /tmp/req.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked pip install -r /tmp/req.txt

COPY gen/python /app/gen/python
COPY agents /app/agents
COPY runtime /app/runtime
COPY observability /app/observability

ENV PYTHONPATH=/app:/app/gen/python
ENV AGENT_PORT=50061
CMD ["python", "agents/navigation/main.py"]
