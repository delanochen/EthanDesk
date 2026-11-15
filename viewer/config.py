"""Viewer-side settings: where the relay lives and how the viewer logs in.

The relay address is edited from the 设置 dialog, so a LAN relay
(``http://127.0.0.1:8080``) and the hosted tunnel (``https://desk.prasinospower.com``)
are equally supported without touching the code.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields

from common.defaults import DEFAULT_VIEWER_URL

FILE_NAME = "viewer.json"


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "deskrelay")


def default_path() -> str:
    return os.path.join(config_dir(), FILE_NAME)


@dataclass
class ViewerConfig:
    relay_url: str = DEFAULT_VIEWER_URL
    password: str = ""
    # Trade resolution/quality for frame rate on a slow link instead of pinning the user's profile.
    auto_quality: bool = True

    @classmethod
    def load(cls, path: str = "") -> "ViewerConfig":
        path = path or default_path()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return cls()
        known = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in raw.items() if key in known})

    def save(self, path: str = "") -> None:
        path = path or default_path()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(asdict(self), handle, indent=2, sort_keys=True)
