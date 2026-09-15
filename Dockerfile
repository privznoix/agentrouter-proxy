# syntax=docker/dockerfile:1

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENTROUTER_PROXY_HOST=0.0.0.0 \
    AGENTROUTER_PROXY_PORT=4020

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agentrouter-proxy.py ./

RUN groupadd -g 1000 appuser \
    && useradd -u 1000 -g appuser --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 4020

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request;req=urllib.request.Request('http://127.0.0.1:4020/health',headers={'Authorization':'Bearer '+os.environ.get('AGENTROUTER_PROXY_API_KEY','local-agentrouter')});urllib.request.urlopen(req,timeout=4)"

CMD ["python", "-m", "uvicorn", "agentrouter-proxy:app", "--host", "0.0.0.0", "--port", "4020", "--workers", "1", "--no-access-log"]
