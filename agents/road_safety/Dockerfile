# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
WORKDIR /app
COPY agents/_sdk/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt
COPY gen/python /app/gen/python
COPY agents /app/agents
COPY runtime /app/runtime
COPY observability /app/observability
ENV PYTHONPATH=/app:/app/gen/python
ENV AGENT_PORT=50072
CMD ["python", "agents/road_safety/main.py"]
