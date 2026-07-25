# build context 为项目根。
FROM python:3.11-slim
WORKDIR /app

COPY proactive/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt

COPY proactive /app/proactive
COPY observability /app/observability

ENV PYTHONPATH=/app
ENV PROACTIVE_HTTP_PORT=50075
CMD ["python", "-m", "proactive.main"]
