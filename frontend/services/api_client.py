import os
from typing import Any

import requests

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")


class APIError(Exception):
    pass


def call_api(
    method: str,
    path: str,
    token: str | None = None,
    payload: dict | None = None,
    files: Any = None,
    form_data: dict | None = None,
    timeout_seconds: int = 20,
) -> Any:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request_kwargs: dict[str, Any]
    if files is not None:
        request_kwargs = {"files": files}
        if form_data is not None:
            request_kwargs["data"] = form_data
    else:
        headers["Content-Type"] = "application/json"
        request_kwargs = {"json": payload}

    response = requests.request(
        method=method,
        url=f"{API_BASE_URL}{path}",
        headers=headers,
        timeout=timeout_seconds,
        **request_kwargs,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise APIError(f"{response.status_code}: {detail}")

    if not response.text:
        return None
    return response.json()


def call_api_bytes(method: str, path: str, token: str | None = None, timeout_seconds: int = 20) -> tuple[bytes, str]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.request(
        method=method,
        url=f"{API_BASE_URL}{path}",
        headers=headers,
        timeout=timeout_seconds,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise APIError(f"{response.status_code}: {detail}")
    return response.content, response.headers.get("content-type", "application/octet-stream")
