# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
WORKDIR /app/memory

COPY memory/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gen/python /app/gen/python
COPY memory /app/memory
COPY runtime /app/runtime

ENV PYTHONPATH=/app:/app/gen/python
ENV MEMORY_PORT=50053
CMD ["python", "main.py"]
