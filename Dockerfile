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

# No build tools needed (pure-Python deps); keep the image small and boring.
WORKDIR /app

COPY webgui/requirements.txt webgui/requirements.txt
RUN pip install --no-cache-dir -r webgui/requirements.txt

# The app, the provisioner, and the config it imports.
COPY scripts/ scripts/
COPY config/ config/
COPY webgui/ webgui/

# Non-root, unprivileged.
RUN useradd --uid 10001 --create-home appuser
USER 10001

ENV REQUIRE_AUTH=true \
    PYTHONUNBUFFERED=1

EXPOSE 8081
# gthread + long timeout so the streaming /provision log (a live subprocess) is
# not cut short. Bind localhost only — oauth2-proxy is the sole network ingress.
CMD ["gunicorn", "--chdir", "/app/webgui", \
     "--bind", "127.0.0.1:8081", \
     "--worker-class", "gthread", "--workers", "1", "--threads", "8", \
     "--timeout", "3600", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "server:app"]
