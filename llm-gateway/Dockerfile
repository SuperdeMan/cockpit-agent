# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
# 国内访问 deb.debian.org（fastly CDN）不稳→换阿里云镜像源，再装 ffmpeg（TTS webm→wav 转码用）。
RUN (sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
     || sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true) \
    && apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app/llm-gateway

COPY llm-gateway/requirements.txt .
# 与上面 apt 换源同一个理由，同一个环境事实：pypi 直连在本机实测约 30KB/s，
# sherpa-onnx（声纹，带 onnxruntime）一个包就要十几分钟。默认走阿里云镜像；
# 需要直连（如 CI/海外）时 `--build-arg PIP_INDEX_URL=https://pypi.org/simple`。
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt

COPY gen/python /app/gen/python
COPY llm-gateway /app/llm-gateway
COPY runtime /app/runtime
COPY observability /app/observability
# M4 P4 声纹模型（gitignore，scripts/fetch-voice-models.* 拉取）。目录靠 .gitkeep 恒存在，
# 故**模型没拉也能构建**——网关启动时决议 voiceprint=disabled，整链逐字回落到 P4 之前。
COPY models /app/models

ENV PYTHONPATH=/app:/app/gen/python
ENV LLM_GATEWAY_PORT=50052
CMD ["python", "main.py"]
