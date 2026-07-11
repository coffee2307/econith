# =============================================================================
# ECONITH :: backend_core image  (Python / FastAPI)
# Multi-stage, slim, non-root. Serves the AI-001 Core Engine API.
# =============================================================================

# ---- base -------------------------------------------------------------------
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ---- builder: install deps into a venv --------------------------------------
FROM base AS builder
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --retries 10 --timeout 300 --no-cache-dir -r requirements.txt

# ---- runtime ----------------------------------------------------------------
FROM base AS runtime
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app

# non-root user
RUN useradd --create-home --uid 1000 econith
COPY --from=builder /opt/venv /opt/venv

# application source (core engine, not the dashboard)
COPY api/ ./api/
COPY ai/ ./ai/
COPY bridges/ ./bridges/
COPY econith/ ./econith/
COPY econith_quant/bridge/ ./econith_quant/bridge/
COPY econith_quant/execution/ ./econith_quant/execution/
COPY econith_quant/recovery/ ./econith_quant/recovery/
COPY quant/ ./quant/
COPY config/ ./config/
COPY core/ ./core/
COPY infrastructure/ ./infrastructure/
COPY sentinel/ ./sentinel/
COPY archive/vendors/manifest.json ./archive/vendors/manifest.json
COPY main.py ./

RUN mkdir -p /app/logs /app/datasets \
  && chown -R econith:econith /app/logs /app/datasets

USER econith
EXPOSE 8000

# Optional local SSL: mount certs to /certs and set SSL_* env to enable.
# Default (behind nothing) serves plain HTTP for the reverse proxy / dev.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8000/api/v1/health'); " || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
