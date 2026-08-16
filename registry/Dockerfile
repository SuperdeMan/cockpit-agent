# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
WORKDIR /app/registry

COPY registry/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked pip install -r requirements.txt

COPY gen/python /app/gen/python
COPY registry /app/registry
COPY observability /app/observability
COPY runtime /app/runtime

ENV PYTHONPATH=/app:/app/gen/python
ENV REGISTRY_PORT=50051
CMD ["python", "-m", "registry.main"]
