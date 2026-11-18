# escrow serve in a container: jobs ping it over HTTP, it watches the clock
# and alerts a webhook. Mount the config read-only and keep the state on a
# volume so a restart doesn't forget when each job last pinged:
#
#   docker run -d -p 8765:8765 -e ESCROW_TOKEN=... \
#     -v ./escrow.yaml:/config/escrow.yaml:ro -v escrow-state:/data \
#     ghcr.io/maximo000/escrow --webhook https://hooks.slack.com/services/...
#
# It listens on 0.0.0.0 inside the container, so it refuses to start without
# ESCROW_TOKEN: anyone who can reach the port could otherwise mark a dead job
# healthy.
FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY escrow/ ./escrow/
RUN pip install --no-cache-dir . && pip cache purge || true

RUN useradd -r -u 10001 -m escrow && mkdir /data && chown 10001 /data
USER 10001
VOLUME /data
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4)"

ENTRYPOINT ["escrow", "serve", "/config/escrow.yaml", "--state", "/data/state.json", "--host", "0.0.0.0", "--port", "8765"]
