# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
# 国内访问 deb.debian.org（fastly CDN）不稳→换阿里云镜像源，再装 ffmpeg（TTS webm→wav 转码用）。
RUN (sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
     || sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true) \
    && apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app/llm-gateway

COPY llm-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gen/python /app/gen/python
COPY llm-gateway /app/llm-gateway
COPY runtime /app/runtime

ENV PYTHONPATH=/app:/app/gen/python
ENV LLM_GATEWAY_PORT=50052
CMD ["python", "main.py"]
