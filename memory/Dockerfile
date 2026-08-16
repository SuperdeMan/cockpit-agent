# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
WORKDIR /app/memory

COPY memory/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked pip install -r requirements.txt

COPY gen/python /app/gen/python
COPY memory /app/memory
COPY runtime /app/runtime
COPY observability /app/observability

ENV PYTHONPATH=/app:/app/gen/python
ENV MEMORY_PORT=50053
CMD ["python", "main.py"]
