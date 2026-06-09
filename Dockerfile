# SPDX-License-Identifier: EUPL-1.2
# openwoo-provisioner — runs scripts/provision.py against a tenant.
#
# OPTIONAL FALLBACK. The default, GitOps-native deploy ships provision.py + the
# config as a ConfigMap on a stock python image — no custom image/registry/token
# (see deploy/). This image is only for edge cases: the config outgrowing the
# 1 MiB ConfigMap limit, or an air-gapped cluster that can't pull python:3-slim.
#
# Pure-stdlib Python, so no pip install / no dependencies — just the script and
# the (tagged) config baked in.
#
# Build:  make image            # or: docker build -t <img>:<tag> .
# Run:    docker run --rm <img> all --base https://<tenant> --user admin --password-env PW
FROM python:3.12-slim

WORKDIR /app
COPY scripts/provision.py /app/scripts/provision.py
COPY config/woo.configuration.json /app/config/woo.configuration.json

# Don't run as root.
USER nobody

# `--config` defaults to config/woo.configuration.json, resolved from WORKDIR.
ENTRYPOINT ["python3", "/app/scripts/provision.py"]
