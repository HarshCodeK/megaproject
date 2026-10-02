# MegaProject — AI Agent OS
#
# Large image: sentence-transformers pulls in torch (~2GB installed). That is
# the honest cost of local embeddings, and it buys offline capability — the
# agent still serves RAG answers with no network and no API key.
#
# Two stages so the runtime layer does not carry the build toolchain.

FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

FROM python:3.13-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MP_DB=/data/megaproject.db \
    MP_CHROMA=/data/chroma_db \
    MP_DOCS=/app/knowledge_docs

# libgomp1 is torch's OpenMP runtime; without it, importing torch aborts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY src/ ./src/
COPY knowledge_docs/ ./knowledge_docs/

# The operator sandbox defaults to WORKSPACE, so it is pinned to /app. That
# means the agent cannot read anything outside this directory by default.
ENV MP_WORKSPACE=/app

RUN mkdir -p /data && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8).status==200 else 1)"

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
