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
# ruby is here for the PNGlitch wrapper. The gem is installed once, at build
# time; nothing reaches rubygems.org when the site is running. pnglitch has no
# runtime dependencies beyond Ruby's own stdlib (zlib, stringio, tempfile), so
# this stays small - no build toolchain, no native extensions.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ruby ruby-rubygems \
 && gem install pnglitch --no-document \
 && apt-get purge -y --auto-remove \
 && rm -rf /var/lib/apt/lists/*

# Fail the build rather than the first request if the gem is not importable.
RUN ruby -e "require 'pnglitch'; puts 'pnglitch ' + PNGlitch::VERSION"

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
