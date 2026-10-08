"""Small stdlib HTTP helpers: polite, retrying GETs that return None on failure."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

UA = "tideline/0.1 (+https://github.com/harshanchenna/tideline)"


def get_bytes(url: str, retries: int = 2, timeout: int = 45, headers: dict | None = None) -> bytes | None:
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 410):
                return None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def get_json(url: str, **kw):
    raw = get_bytes(url, headers={"Accept": "application/json"}, **kw)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
