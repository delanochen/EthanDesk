"""用 Windows 账户凭据给远程会话做门禁。

只验证「这个账户在这台机器上是否存在、密码对不对」，不会创建登录会话、不改当前
桌面——EthanDesk 镜像是当前控制台桌面，所以凭据是门禁，不是切换到别的账户。

实现走 ``LogonUserW``（LOGON32_LOGON_NETWORK）：不需要管理员权限，也不会像
``CreateProcessWithLogonW`` 那样留下会话或进程。
"""

from __future__ import annotations

import ctypes
import sys

LOGON32_LOGON_NETWORK = 3
LOGON32_PROVIDER_DEFAULT = 0

ACCOUNT_NOT_ALLOWED = "account_not_allowed"
BAD_CREDENTIALS = "bad_credentials"


def normalise_account(name: str) -> str:
    """``MACHINE\\user`` / ``.\\user`` / ``user`` 统一成 ``user``，用于白名单比对。"""
    text = str(name or "").strip()
    if "\\" in text:
        text = text.split("\\", 1)[1]
    return text.strip().lower()


def allowed(user: str, accounts: list[str] | None) -> bool:
    """名单为空表示放行本机任意账户；否则必须是名单里的那一个。"""
    if not accounts:
        return True
    return normalise_account(user) in {normalise_account(item) for item in accounts}


def verify(user: str, password: str, accounts: list[str] | None = None) -> tuple[bool, str]:
    """返回 ``(是否通过, 原因码)``。原因码可以直接回给主控端。"""
    if not str(user or "").strip() or not str(password or ""):
        return False, BAD_CREDENTIALS
    if not allowed(user, accounts):
        return False, ACCOUNT_NOT_ALLOWED
    if sys.platform != "win32":
        # 非 Windows 上无法验证，宁可拒绝也不假装通过。
        return False, BAD_CREDENTIALS

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    # 显式声明参数类型，免得 64 位下指针被当成 int 截断。
    advapi32.LogonUserW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.LogonUserW.restype = ctypes.c_int
    token = ctypes.c_void_p()
    ok = advapi32.LogonUserW(
        ctypes.c_wchar_p(str(user).strip()),
        ctypes.c_wchar_p("."),
        ctypes.c_wchar_p(str(password)),
        LOGON32_LOGON_NETWORK,
        LOGON32_PROVIDER_DEFAULT,
        ctypes.byref(token),
    )
    if not ok:
        return False, BAD_CREDENTIALS
    try:
        advapi32.CloseHandle(token)
    except OSError:
        pass
    return True, ""
