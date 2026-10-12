from __future__ import annotations

import json
from typing import Any, Mapping


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


def decode_json_content(content: bytes) -> Any:
    """Decode JSON bytes returned by sites that may use UTF-8 or GB18030."""
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return json.loads(content.decode(encoding))
        except (UnicodeDecodeError, ValueError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("Unable to decode JSON response.")


def fetch_json(
    url: str,
    session: Any = None,
    timeout: float = 20.0,
    headers: Mapping[str, str] | None = None,
) -> Any:
    """Fetch JSON with the shared User-Agent and encoding fallback."""
    request_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        request_headers.update(headers)

    try:
        if session is not None:
            response = session.get(url, timeout=timeout, headers=request_headers)
        else:
            import requests

            response = requests.get(url, timeout=timeout, headers=request_headers)
        response.raise_for_status()
    except Exception as exc:
        raise RuntimeError("Unable to fetch %s: %s" % (url, exc)) from exc

    content = getattr(response, "content", None)
    if content is None:
        return response.json()
    return decode_json_content(content)
