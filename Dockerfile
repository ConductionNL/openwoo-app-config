# SPDX-License-Identifier: EUPL-1.2
# Control-plane image: the Flask provisioning web GUI (webgui/server.py) plus the
# provisioner it shells out to (scripts/provision.py) and the WOO config it ships.
#
# Runs gunicorn bound to 127.0.0.1:8081 — NOT exposed directly. In the pod,
# oauth2-proxy (a separate container) is the only listener on the network and
# proxies authenticated requests to this localhost port. See webgui/deploy/.
#
# Build (from repo root):  docker build -t <registry>/openwoo-provisioner:<tag> .
FROM python:3.12-slim

# git is a RUNTIME dependency of the handbook content layer (docs_mcp shallow-
# clones the component repos on demand); ca-certificates for HTTPS to
# github.com and api.anthropic.com. Note: claude-agent-sdk ships a bundled
# standalone `claude` CLI (~250 MB) — the image is deliberately fat, not broken.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY webgui/requirements.txt webgui/requirements.txt
RUN pip install --no-cache-dir -r webgui/requirements.txt

# Handbook content layer: the hub repo, PINNED to a sha (same pin philosophy as
# the techbook gates). The build FAILS if hub main moved past the pin — bumping
# is a conscious act: new sha here + CHANGELOG entry.
# NB migratie 2026-07-17: bron is GitHub; vereist dat ConductionNL/hub bestaat
# (docs/sites-batch) vóór de eerstvolgende image-build. De sha-pin is
# host-onafhankelijk (zelfde commit op beide hosts).
ARG HUB_REPO=https://github.com/ConductionNL/hub.git
ARG HUB_SHA=27cc04e818ffe33f864540e5dcbd5155b3e212d4
RUN git clone --depth 1 "$HUB_REPO" /opt/hub \
 && [ "$(git -C /opt/hub rev-parse HEAD)" = "$HUB_SHA" ] \
 && rm -rf /opt/hub/.git

# The app, the provisioner, and the config it imports.
COPY scripts/ scripts/
COPY config/ config/
COPY webgui/ webgui/

# Non-root, unprivileged.
RUN useradd --uid 10001 --create-home appuser
USER 10001

ENV REQUIRE_AUTH=true \
    PYTHONUNBUFFERED=1 \
    HUB_DIR=/opt/hub \
    DOCS_MCP_CACHE=/var/cache/docs-mcp

EXPOSE 8081
# gthread + long timeout so the streaming /provision log (a live subprocess) is
# not cut short. Bind localhost only — oauth2-proxy is the sole network ingress.
CMD ["gunicorn", "--chdir", "/app/webgui", \
     "--bind", "127.0.0.1:8081", \
     "--worker-class", "gthread", "--workers", "1", "--threads", "8", \
     "--timeout", "3600", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "server:app"]
