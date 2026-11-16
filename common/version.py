"""版本号与更新检查的公开入口。

主控端启动后要自己看一眼有没有新版本；版本号只在这里维护一份，
打包出来的 exe 和源码跑的是同一个值。
"""

from __future__ import annotations

import os

APP_NAME = "EthanDesk"
APP_VERSION = "1.3.0"

# 仓库根目录的 VERSION 文件就是唯一的「最新版本」来源，改它即发布新版本。
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/delanochen/EthanDesk/main/VERSION"
RELEASES_URL = "https://github.com/delanochen/EthanDesk/releases"


def app_version() -> str:
    """当前版本；环境变量可覆盖，方便在测试或灰度里假装成旧版本。"""
    override = os.environ.get("ETHANDESK_APP_VERSION", "").strip()
    return override or APP_VERSION
