from __future__ import annotations

import json
from typing import Any, cast

import httpx
from logerr import Result
from logerr.recipes.retry import on_err
from logerr.utilities import execute
from tenacity import stop_after_attempt, wait_exponential


@on_err(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
    log_attempts=True,
)
def list_template_names(
    client: httpx.Client, repo: str, branch: str
) -> Result[list[str], Exception]:
    def _fetch() -> list[str]:
        url = f"https://api.github.com/repos/{repo}/contents/templates?ref={branch}"
        response = client.get(url)
        response.raise_for_status()
        entries = response.json()
        return sorted(entry["name"] for entry in entries if entry["type"] == "dir")

    return cast(Result[list[str], Exception], execute(_fetch))


@on_err(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
    log_attempts=True,
)
def fetch_template(
    client: httpx.Client, repo: str, branch: str, name: str
) -> Result[dict[str, Any], Exception]:
    def _fetch() -> dict[str, Any]:
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/templates/{name}/devcontainer.json"
        response = client.get(url)
        response.raise_for_status()
        return cast(dict[str, Any], json.loads(response.text))

    return cast(Result[dict[str, Any], Exception], execute(_fetch))
