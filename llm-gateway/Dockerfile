# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app/llm-gateway

COPY llm-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gen/python /app/gen/python
COPY llm-gateway /app/llm-gateway

ENV PYTHONPATH=/app/gen/python
ENV LLM_GATEWAY_PORT=50052
CMD ["python", "main.py"]
