# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
WORKDIR /app
COPY agents/_sdk/requirements.txt /tmp/req.txt
COPY agents/mcp_bridge/requirements.txt /tmp/req-mcp.txt
RUN pip install --no-cache-dir -r /tmp/req.txt -r /tmp/req-mcp.txt
COPY gen/python /app/gen/python
COPY agents /app/agents
COPY runtime /app/runtime
COPY observability /app/observability
COPY scripts/e2e_identity.py /app/scripts/e2e_identity.py
ENV PYTHONPATH=/app:/app/gen/python
# MCP stdio 帧是 UTF-8 JSON——子进程编码不靠环境撞运气
ENV PYTHONIOENCODING=utf-8
ENV AGENT_PORT=50076
CMD ["python", "agents/mcp_bridge/main.py"]
