# ---------------------------------------------------------------------------
# Web application: FastAPI + the real pixelsort package.
#
# python:3.12-slim rather than alpine: Pillow ships manylinux wheels for glibc,
# so this image builds in seconds. On alpine, pip has to compile Pillow from
# source, which needs a full toolchain and turns a 20-second build into minutes.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Pillow's wheels cover the common formats; libjpeg/zlib come along with them.
# curl is here only so the compose healthcheck has something to call.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY content ./content

# Run unprivileged. The data volume is chowned to this uid in compose.
RUN useradd --create-home --uid 10001 site \
 && mkdir -p /data/UploadedImages /data/results /data/db \
 && chown -R site:site /app /data
USER site

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
