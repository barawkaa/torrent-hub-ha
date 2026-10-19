# Stable Prowlarr runtime from maintained LinuxServer ARM64 image.
ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.22
FROM lscr.io/linuxserver/prowlarr:latest AS prowlarr
FROM ${BUILD_FROM}
RUN apk add --no-cache icu-libs icu-data-full libstdc++ libgcc sqlite-libs zlib openssl curl ca-certificates tzdata
COPY --from=prowlarr /app/prowlarr/bin/ /opt/prowlarr/
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt
COPY app/ /app/
# Pin a curated, offline-capable subset at IMAGE BUILD time, not runtime.
# Upstream maintained community definitions: https://github.com/Prowlarr/Indexers
# The build fails rather than shipping empty or broken tracker definitions.
ARG INDEXERS_REF=master
RUN mkdir -p /app/definitions && \
    for name in rutor noname-club megapeer kinozal rustorka; do \
      curl --fail --location --show-error --retry 4 --connect-timeout 12 \
      "https://raw.githubusercontent.com/Prowlarr/Indexers/${INDEXERS_REF}/definitions/v11/${name}.yml" \
      -o "/app/definitions/${name}.yml" || exit 1; \
      grep -q '^id:' "/app/definitions/${name}.yml" || exit 1; \
    done
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=3)"
CMD ["python3", "/app/runner.py"]
