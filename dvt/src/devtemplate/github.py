from __future__ import annotations

import json
from typing import Any, cast

import httpx


def list_template_names(client: httpx.Client, repo: str, branch: str) -> list[str]:
    url = f"https://api.github.com/repos/{repo}/contents/templates?ref={branch}"
    response = client.get(url)
    response.raise_for_status()
    entries = response.json()
    return sorted(entry["name"] for entry in entries if entry["type"] == "dir")


def fetch_template(client: httpx.Client, repo: str, branch: str, name: str) -> dict[str, Any]:
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/templates/{name}/devcontainer.json"
    response = client.get(url)
    response.raise_for_status()
    return cast(dict[str, Any], json.loads(response.text))
