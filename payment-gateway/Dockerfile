FROM python:3.11-slim
WORKDIR /app/payment-gateway

COPY payment-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gen/python /app/gen/python
COPY payment-gateway /app/payment-gateway

ENV PYTHONPATH=/app/gen/python
ENV PAYMENT_PORT=50071
CMD ["python", "main.py"]
