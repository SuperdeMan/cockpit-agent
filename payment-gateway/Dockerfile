FROM python:3.11-slim
WORKDIR /app/payment-gateway

COPY payment-gateway/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked pip install -r requirements.txt

COPY gen/python /app/gen/python
COPY payment-gateway /app/payment-gateway
COPY runtime /app/runtime
COPY observability /app/observability
COPY security /app/security

ENV PYTHONPATH=/app:/app/gen/python
ENV PAYMENT_PORT=50071
CMD ["python", "main.py"]
