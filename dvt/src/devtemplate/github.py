from __future__ import annotations

import json
from typing import Any

import httpx
from logerr import Err, Ok, Result
from logerr.recipes.retry import on_err
from tenacity import stop_after_attempt, wait_exponential


@on_err(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
    log_attempts=True,
)
def list_template_names(client: httpx.Client, repo: str, branch: str) -> Result[list[str], Exception]:
    try:
        url = f"https://api.github.com/repos/{repo}/contents/templates?ref={branch}"
        response = client.get(url)
        response.raise_for_status()
        entries = response.json()
        return Ok(sorted(entry["name"] for entry in entries if entry["type"] == "dir"))
    except Exception as exc:
        return Err(exc)


@on_err(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
    log_attempts=True,
)
def fetch_template(
    client: httpx.Client, repo: str, branch: str, name: str
) -> Result[dict[str, Any], Exception]:
    try:
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/templates/{name}/devcontainer.json"
        response = client.get(url)
        response.raise_for_status()
        return Ok(json.loads(response.text))
    except Exception as exc:
        return Err(exc)
