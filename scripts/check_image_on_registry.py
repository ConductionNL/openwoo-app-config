#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/check_image_on_registry.py — verifieer dat een image-tag écht op de
# registry staat. Vangnet na `docker push`: drie pushes faalden stil
# (auth/rechten) terwijl de operator dacht dat ze geland waren, waarna Argo
# naar een niet-bestaande tag rolde (ImagePullBackOff, 2026-07-14).
#
# Twee registries, twee API's:
#   * ghcr.io    — het doel voor eigen images (cluster-config/docs/mirror.md:
#                  "eigen images horen vanuit hun eigen build-pipelines naar
#                  ghcr.io/conductionnl gepubliceerd te worden"). Anonieme
#                  token + manifest-HEAD, precies de recept uit dat document.
#   * docker.io  — de oude plek; blijft werken zolang er nog tags leven.
#
# Een 401 op ghcr betekent bijna altijd "package staat op privé", niet "tag
# bestaat niet": ghcr zet nieuwe packages standaard op privé. Dat onderscheid
# staat in de foutmelding, want de fix verschilt volledig.
#
# Writes: read-only (anonieme registry-API-calls)
# Idempotent: ja
# Requires: python3 (stdlib), netwerk naar ghcr.io / hub.docker.com
#
# Usage:
#   python3 scripts/check_image_on_registry.py ghcr.io/conductionnl/openwoo-provisioner:0.6.0
#   python3 scripts/check_image_on_registry.py docker.io/conduction2022/openwoo-provisioner:0.5.0
#   python3 scripts/check_image_on_registry.py conduction2022/openwoo-provisioner:0.5.0
#   make push IMAGE=...   # roept dit script automatisch aan
"""Faalt hard (exit 1) als de tag niet op de registry bestaat."""

import json
import sys
import urllib.error
import urllib.request

_MANIFEST_ACCEPT = ",".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


def _check_ghcr(repo, tag):
    """Anonymous manifest lookup against ghcr, per cluster-config/docs/mirror.md."""
    token_url = (f"https://ghcr.io/token?scope=repository:{repo}:pull"
                 f"&service=ghcr.io")
    try:
        with urllib.request.urlopen(token_url, timeout=15) as resp:
            token = json.load(resp).get("token")
    except urllib.error.URLError as exc:
        print(f"FOUT: ghcr niet bereikbaar: {exc}", file=sys.stderr)
        return 1
    if not token:
        print("FOUT: ghcr gaf geen anoniem token terug", file=sys.stderr)
        return 1

    req = urllib.request.Request(f"https://ghcr.io/v2/{repo}/manifests/{tag}",
                                 method="HEAD")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", _MANIFEST_ACCEPT)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            digest = resp.headers.get("Docker-Content-Digest", "?")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            print(f"FOUT: geen anonieme toegang tot {repo}:{tag} — de package "
                  f"staat waarschijnlijk nog op PRIVÉ. Zet hem op public, "
                  f"anders heeft elke namespace een pull-secret nodig.",
                  file=sys.stderr)
            return 1
        if exc.code == 404:
            print(f"FOUT: {repo}:{tag} staat NIET op ghcr — de push is niet "
                  f"geland (check login/rechten)", file=sys.stderr)
            return 1
        print(f"FOUT: registry-check kreeg HTTP {exc.code}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"FOUT: ghcr niet bereikbaar: {exc.reason}", file=sys.stderr)
        return 1
    print(f"registry OK: ghcr.io/{repo}:{tag} ({digest})")
    return 0


def _check_dockerhub(repo, tag):
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"FOUT: {repo}:{tag} staat NIET op de registry — "
                  "de push is niet geland (check login/rechten)",
                  file=sys.stderr)
            return 1
        print(f"FOUT: registry-check kreeg HTTP {exc.code}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"FOUT: registry niet bereikbaar: {exc.reason}", file=sys.stderr)
        return 1
    print(f"registry OK: {repo}:{data['name']} "
          f"(gepusht {data.get('tag_last_pushed', '?')[:19]})")
    return 0


def main() -> int:
    if len(sys.argv) != 2 or ":" not in sys.argv[1]:
        print("usage: check_image_on_registry.py <repo>:<tag>", file=sys.stderr)
        return 2
    ref = sys.argv[1]
    if ref.startswith("ghcr.io/"):
        repo, tag = ref.removeprefix("ghcr.io/").rsplit(":", 1)
        return _check_ghcr(repo, tag)
    repo, tag = ref.removeprefix("docker.io/").rsplit(":", 1)
    return _check_dockerhub(repo, tag)


if __name__ == "__main__":
    sys.exit(main())
