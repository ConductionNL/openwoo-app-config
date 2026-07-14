#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/check_image_on_registry.py — verifieer dat een image-tag écht op
# Docker Hub staat. Vangnet na `docker push`: drie pushes faalden stil
# (auth/rechten) terwijl de operator dacht dat ze geland waren, waarna Argo
# naar een niet-bestaande tag rolde (ImagePullBackOff, 2026-07-14).
#
# Writes: read-only (anonieme Hub-API-call)
# Idempotent: ja
# Requires: python3 (stdlib), netwerk naar hub.docker.com
#
# Usage:
#   python3 scripts/check_image_on_registry.py docker.io/conduction2022/openwoo-provisioner:0.3.2
#   python3 scripts/check_image_on_registry.py conduction2022/openwoo-provisioner:0.3.2
#   make push IMAGE=...   # roept dit script automatisch aan
"""Faalt hard (exit 1) als de tag niet op Docker Hub bestaat."""

import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    if len(sys.argv) != 2 or ":" not in sys.argv[1]:
        print("usage: check_image_on_registry.py <repo>:<tag>", file=sys.stderr)
        return 2
    ref = sys.argv[1].removeprefix("docker.io/")
    repo, tag = ref.rsplit(":", 1)
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


if __name__ == "__main__":
    sys.exit(main())
