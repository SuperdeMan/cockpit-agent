# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
WORKDIR /app
COPY agents/_sdk/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt
COPY agents/scene_orchestrator/requirements.txt /tmp/req-scene.txt
RUN pip install --no-cache-dir -r /tmp/req-scene.txt

COPY gen/python /app/gen/python
COPY agents /app/agents
COPY runtime /app/runtime
COPY observability /app/observability

# 动作白名单的**唯一真相源**（D3）：VAL 知识库随镜像 COPY，云侧不维护第二份对象/操作表。
# 0.1.0 的场景词表与 VAL 漂移导致动作静默失效（roadmap §8），这条 COPY 是根治点——
# 漏了它 src/catalog.py 会诚实抛 CatalogError（而不是静默跑在空词表上）。
COPY orchestrator/edge/knowledge/commands.yaml orchestrator/edge/knowledge/entities.yaml \
     /app/agents/scene_orchestrator/knowledge/

ENV PYTHONPATH=/app:/app/gen/python
ENV AGENT_PORT=50069
CMD ["python", "agents/scene_orchestrator/main.py"]
