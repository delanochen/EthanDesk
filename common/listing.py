"""目录列举：被控端给主控端看、主控端本地栏看自己，都用这一份逻辑。

返回 ``(entries, directory, error)``；``entries`` 每项是
``{"name": str, "dir": bool, "size": int}``，目录在前、同类按名字升序。
"""

from __future__ import annotations

import os

DEFAULT_LIMIT = 500


def list_directory(raw: str, limit: int = DEFAULT_LIMIT) -> tuple[list[dict], str, str]:
    text = str(raw or "").strip().strip('"').strip("'")
    if "~" in text or "%" in text:
        text = os.path.expandvars(os.path.expanduser(text))
    if not text:
        drives: list[dict] = []
        if os.name == "nt":
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                root = f"{letter}:\\"
                if os.path.isdir(root):
                    drives.append({"name": root, "dir": True, "size": 0})
        if not drives:
            return [], "", "没有可访问的磁盘"
        return drives, "", ""
    if not os.path.isabs(text):
        return [], "", f"只接受绝对路径：{raw}"
    if not os.path.isdir(text):
        return [], "", f"目录不存在：{raw}"
    entries: list[dict] = []
    try:
        with os.scandir(text) as iterator:
            for item in iterator:
                try:
                    is_dir = item.is_dir()
                    size = 0 if is_dir else item.stat().st_size
                except OSError:
                    continue
                entries.append({"name": item.name, "dir": is_dir, "size": size})
                if len(entries) >= limit:
                    break
    except OSError as exc:
        return [], text, f"读取目录失败：{exc}"
    entries.sort(key=lambda entry: (not entry["dir"], entry["name"].lower()))
    return entries, text, ""


def parent_of(path: str) -> str:
    """上一级；盘符根（``C:\\``）退回顶层列表（空串）。"""
    text = str(path or "").strip()
    if not text:
        return ""
    trimmed = text.rstrip("\\/")
    if len(trimmed) <= 2 and trimmed.endswith(":"):
        return ""
    parent = os.path.dirname(trimmed)
    return parent
