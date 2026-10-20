ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.22
FROM ${BUILD_FROM}
ARG BUILD_VERSION=2.0.0
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="aarch64" \
  org.opencontainers.image.source="https://github.com/barawkaa/torrent-hub-ha"
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt
COPY app/ /app/
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=3)"
CMD ["python3","-m","uvicorn","main:app","--host","0.0.0.0","--port","8765"]
