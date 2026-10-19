# Stable Prowlarr runtime from maintained LinuxServer ARM64 image.
FROM lscr.io/linuxserver/prowlarr:latest AS prowlarr
ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.22
FROM ${BUILD_FROM}
RUN apk add --no-cache icu-libs icu-data-full libstdc++ libgcc sqlite-libs zlib openssl curl ca-certificates tzdata
COPY --from=prowlarr /app/prowlarr/bin/ /opt/prowlarr/
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt
COPY app/ /app/
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=3)"
CMD ["python3", "/app/runner.py"]
