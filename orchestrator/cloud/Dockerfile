# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
WORKDIR /app/orchestrator/cloud

COPY orchestrator/cloud/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gen/python /app/gen/python
COPY orchestrator/cloud /app/orchestrator/cloud

ENV PYTHONPATH=/app/gen/python
ENV CLOUD_PLANNER_PORT=50054
CMD ["python", "main.py"]
