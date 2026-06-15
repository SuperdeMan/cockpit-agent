FROM python:3.11-slim

WORKDIR /app
COPY observability/collector/requirements.txt ./req.txt
RUN pip install --no-cache-dir -r req.txt
COPY observability/ ./observability/

ENV PYTHONPATH=/app
EXPOSE 8092

CMD ["python", "-m", "observability.collector.main"]
