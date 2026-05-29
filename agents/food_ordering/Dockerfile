# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
WORKDIR /app

COPY agents/_sdk/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt

COPY gen/python /app/gen/python
COPY agents /app/agents

ENV PYTHONPATH=/app:/app/gen/python
ENV AGENT_PORT=50063
CMD ["python", "agents/food_ordering/main.py"]
