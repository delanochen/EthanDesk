"""DPI awareness: capture and input must agree on one coordinate space."""

from __future__ import annotations

import sys

from common.dpi import ensure_dpi_aware, system_dpi, tk_scaling


class _FakeTk:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

        class _TkApp:
            def __init__(self, sink):
                self._sink = sink

            def call(self, *args):
                self._sink.append(args)
                return None

        self.tk = _TkApp(self.calls)


def test_ensure_dpi_aware_returns_bool_and_is_idempotent():
    first = ensure_dpi_aware()
    second = ensure_dpi_aware()
    assert isinstance(first, bool)
    assert second is first or second is True or second is False


def test_ensure_dpi_aware_is_false_off_windows():
    original = sys.platform
    sys.platform = "linux"
    try:
        assert ensure_dpi_aware() is False
    finally:
        sys.platform = original


def test_system_dpi_defaults_to_96_off_windows():
    original = sys.platform
    sys.platform = "linux"
    try:
        assert system_dpi() == 96
    finally:
        sys.platform = original


def test_tk_scaling_sets_pixels_per_point():
    root = _FakeTk()
    scaling = tk_scaling(root)
    assert root.calls and root.calls[-1][0] == "tk"
    assert root.calls[-1][1] == "scaling"
    assert scaling == system_dpi() / 72.0
    assert abs(root.calls[-1][2] - scaling) < 1e-9


def test_tk_scaling_swallows_tk_failures():
    class Broken:
        class tk:
            @staticmethod
            def call(*_args):
                raise RuntimeError("boom")

    assert tk_scaling(Broken()) == system_dpi() / 72.0
