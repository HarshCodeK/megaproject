FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# libgomp1 is torch's OpenMP runtime; sentence-transformers needs it at import.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ ./src/
COPY knowledge_docs/ ./knowledge_docs/
COPY app.py ./

RUN useradd --create-home --uid 1000 appuser && mkdir -p /data && chown -R appuser /app /data
USER appuser

ENV MP_DB=/data/megaproject.db MP_CHROMA=/data/chroma_db

# The agent's sandbox defaults to the repository root, so pinned to /app: the
# agent cannot read anything outside this directory unless told otherwise.
ENV MP_WORKSPACE=/app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=10).status==200 else 1)"

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
