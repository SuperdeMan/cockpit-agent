# build context 为项目根（见 deploy/docker-compose.yaml）。需先 `make proto` 生成 gen/python。
FROM python:3.11-slim
WORKDIR /app

COPY agents/_sdk/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt

COPY gen/python /app/gen/python
COPY agents /app/agents

ENV PYTHONPATH=/app:/app/gen/python
ENV AGENT_PORT=50061
CMD ["python", "agents/navigation/main.py"]
