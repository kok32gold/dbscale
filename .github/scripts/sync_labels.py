#!/usr/bin/env python3
"""Create or update GitHub issue labels from .github/labels.json.

Existing labels that are not listed here are left alone.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / ".github" / "labels.json"


def request(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, bytes]:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> None:
    token = os.environ["GITHUB_TOKEN"]
    repository = os.environ["GITHUB_REPOSITORY"]
    labels = json.loads(LABELS.read_text())
    api = f"https://api.github.com/repos/{repository}/labels"
    for label in labels:
        body = {
            "name": label["name"],
            "color": label["color"],
            "description": label["description"],
        }
        encoded = quote(label["name"], safe="")
        status, _payload = request("GET", f"{api}/{encoded}", token)
        if status == 200:
            status, raw = request("PATCH", f"{api}/{encoded}", token, body)
        elif status == 404:
            status, raw = request("POST", api, token, body)
        else:
            raise SystemExit(f"lookup failed for {label['name']}: {status}")
        if status not in (200, 201):
            raise SystemExit(f"sync failed for {label['name']}: {status} {raw!r}")
        print(f"synced {label['name']}")


if __name__ == "__main__":
    main()
