"""更新检查：把「有没有新版本」做成纯函数，网络那一步单独注进去好测。"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Callable

from common.version import RELEASES_URL, UPDATE_MANIFEST_URL

USER_AGENT = "EthanDesk/update-check"

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$", re.IGNORECASE)


def parse_version(text: str) -> tuple[int, ...]:
    """``"v1.3.1"`` → ``(1, 3, 1)``；不是版本号就抛 ValueError。"""
    match = _VERSION_RE.match(str(text or "").strip())
    if match is None:
        raise ValueError(f"not a version: {text!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    """候选版本是否比当前新；补齐位数后按段比较，``1.10 > 1.9``。"""
    left = parse_version(candidate)
    right = parse_version(current)
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left > right


def parse_manifest(text: str) -> dict[str, str]:
    """VERSION 文件允许两种写法：裸版本号，或带 url/notes 的 JSON。"""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty manifest")
    if raw.startswith("{"):
        payload = json.loads(raw)
        if not isinstance(payload, dict) or "version" not in payload:
            raise ValueError("manifest has no version")
        return {
            "version": str(payload.get("version", "")).strip(),
            "url": str(payload.get("url") or RELEASES_URL).strip(),
            "notes": str(payload.get("notes") or "").strip(),
        }
    return {"version": raw.splitlines()[0].strip(), "url": RELEASES_URL, "notes": ""}


def fetch_manifest(url: str = UPDATE_MANIFEST_URL, timeout: float = 8.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", "replace")


def check_update(
    current: str,
    url: str = UPDATE_MANIFEST_URL,
    fetcher: Callable[[str], str] = fetch_manifest,
) -> dict[str, Any] | None:
    """有新版本返回 ``{"version","url","notes","current"}``，否则 ``None``。

    网络失败、清单坏了都按「没有更新」处理：启动时不该因为一次联网失败打扰用户。
    """
    try:
        manifest = parse_manifest(fetcher(url))
        latest = manifest["version"]
        if not is_newer(latest, current):
            return None
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return {
        "version": latest,
        "url": manifest["url"],
        "notes": manifest["notes"],
        "current": current,
    }
