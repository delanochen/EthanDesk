"""Contract tests for the unified AweSun-style client."""

from __future__ import annotations


def test_module_imports_without_display():
    import desk.client as client

    assert client.PAGES == ("远程协助", "设备", "会话", "设置")
    assert client.SETTINGS_PAGES == ("本机信息", "常规", "安全", "网络", "画面", "版本")


def test_mask_code_hides_digits():
    from desk.client import mask_code

    assert mask_code("246810") == "••• •••"
    assert mask_code("") == ""
    assert mask_code("12345") == "••• •••"


def test_connect_button_label_matches_state():
    from desk.client import connect_label

    assert connect_label(True) == "断开"
    assert connect_label(False) == "连接"


def test_window_builds_every_page():
    import tkinter as tk

    import pytest

    from desk.client import PAGES, DeskWindow

    try:
        window = DeskWindow(start_threads=False)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    try:
        assert set(window.pages) == set(PAGES)
        for frame in window.pages.values():
            assert frame.winfo_exists()
    finally:
        window.root.destroy()


def _labels(widget) -> list:
    """Collect every user-visible text under a page."""
    texts = []
    try:
        texts.append(str(widget.cget("text")))
    except Exception:
        pass
    for child in widget.winfo_children():
        texts.extend(_labels(child))
    return texts


def test_home_page_has_one_serve_switch_and_no_manual_buttons():
    import tkinter as tk

    import pytest

    from desk.client import DeskWindow

    try:
        window = DeskWindow(start_threads=False)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    try:
        texts = _labels(window.pages["远程协助"])
        assert "允许他人协助本机" in texts
        assert "开始服务" not in texts, "手动启停不该出现在首页"
        assert "停止服务" not in texts, "手动启停不该出现在首页"
        assert window.serve_status.get() == "已关闭"
    finally:
        window.root.destroy()


def test_serve_switch_starts_and_stops_the_agent():
    import tkinter as tk

    import pytest

    from desk.client import DeskWindow

    calls = []
    try:
        window = DeskWindow(start_threads=False)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    window.agent_worker.start = lambda: calls.append("start")
    window.agent_worker.stop = lambda: calls.append("stop")
    try:
        window.serve_var.set(True)
        window._toggle_serve()
        assert calls == ["start"]
        assert window.serve_status.get() == "正在连接中继…"

        window._relay_state = "online"
        window._refresh_serve_status()
        assert window.serve_status.get() == "可接收协助"

        window.serve_var.set(False)
        window._toggle_serve()
        assert calls == ["start", "stop"]
        assert window.serve_status.get() == "已关闭"
    finally:
        window.root.destroy()


def test_manual_start_stop_lives_in_advanced_settings():
    """首页不放手动手停；只有在“设置·常规·高级”里保留排查用按钮。"""
    import tkinter as tk

    import pytest

    from desk.client import DeskWindow

    try:
        window = DeskWindow(start_threads=False)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    try:
        texts = _labels(window.pages["设置"])
        assert "手动启动服务" in texts
        assert "手动停止服务" in texts
        home = _labels(window.pages["远程协助"])
        assert "手动启动服务" not in home
    finally:
        window.root.destroy()


def test_relay_online_event_is_what_turns_the_label_receivable():
    """“可接收协助”必须由中继上报的 online 事件驱动，而不是点了开关就算。"""
    import tkinter as tk

    import pytest

    from desk.client import DeskWindow

    try:
        window = DeskWindow(start_threads=False)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    window.agent_worker.start = lambda: None
    window.agent_worker.stop = lambda: None
    try:
        window.serve_var.set(True)
        window._toggle_serve()
        assert window.serve_status.get() == "正在连接中继…"

        window.agent_events.put(("status", "online"))
        window._drain()
        assert window.serve_status.get() == "可接收协助"

        window.agent_events.put(("status", "offline"))
        window._drain()
        assert window.serve_status.get() != "可接收协助"

        # 开关关掉后，即使中继还报在线，也必须显示已关闭
        window.serve_var.set(False)
        window._toggle_serve()
        window.agent_events.put(("status", "online"))
        window._drain()
        assert window.serve_status.get() == "已关闭"
    finally:
        window.root.destroy()



def test_window_title_carries_the_client_version():
    import tkinter as tk

    import pytest

    from common.version import APP_VERSION
    from desk.client import DeskWindow

    try:
        window = DeskWindow(start_threads=False)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    try:
        assert APP_VERSION in window.root.title()
    finally:
        window.root.destroy()


def test_settings_has_a_version_page_with_the_current_build():
    import tkinter as tk

    import pytest

    from common.version import APP_VERSION
    from desk.client import DeskWindow

    try:
        window = DeskWindow(start_threads=False)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    try:
        texts = _labels(window.pages["设置"])
        assert "检查更新" in texts
        assert APP_VERSION in window.version_note.get()
    finally:
        window.root.destroy()


def test_close_hides_to_tray_instead_of_stopping_the_service():
    """有托盘时关窗口只收起：被控端开关不能因为关窗被悄悄关掉。"""
    import tkinter as tk

    import pytest

    from desk.client import DeskWindow

    calls = []
    try:
        window = DeskWindow(start_threads=False)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    window.agent_worker.stop = lambda: calls.append("stop")
    window.viewer_worker.stop = lambda: calls.append("viewer-stop")

    class FakeTray:
        def notify(self, *_args, **_kwargs):
            pass

        def stop(self):
            calls.append("tray-stop")

    try:
        window.tray = FakeTray()
        window._on_close()          # 等于点窗口右上角
        assert calls == [], "关窗不该停服务"
        assert window.root.state() == "withdrawn"

        window._quit_from_tray()    # 托盘菜单里的退出才算真退出
        assert "stop" in calls and "viewer-stop" in calls and "tray-stop" in calls
    finally:
        window.tray = None
        try:
            window.root.destroy()
        except tk.TclError:
            pass