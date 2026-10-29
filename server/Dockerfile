FROM python:3.12-slim
RUN groupadd --gid 10001 health && useradd --uid 10001 --gid health --create-home health \
    && mkdir -p /data/health /data/secrets && chown -R health:health /data
WORKDIR /app
COPY server/health_ingest.py /app/health_ingest.py
ENV PYTHONUNBUFFERED=1 HEALTH_INGEST_HOST=0.0.0.0 HEALTH_INGEST_PORT=8799 \
    HEALTH_INGEST_BASE=/data/secrets HEALTH_DATA_ROOT=/data/health HEALTH_TIMEZONE=UTC
USER health
EXPOSE 8799
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8799/health/ping', timeout=3)"
CMD ["python", "/app/health_ingest.py"]
