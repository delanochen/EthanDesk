import pytest

from common.coords import (
    norm_to_absolute_units,
    norm_to_pixel,
    pixel_to_norm,
)


def test_norm_to_pixel_maps_corners():
    assert norm_to_pixel(0.0, 0.0, 0, 0, 1920, 1080) == (0, 0)
    assert norm_to_pixel(1.0, 1.0, 0, 0, 1920, 1080) == (1919, 1079)


def test_norm_to_pixel_honours_virtual_offset():
    assert norm_to_pixel(0.0, 0.0, -1920, 0, 3840, 1080) == (-1920, 0)
    assert norm_to_pixel(0.5, 0.5, -1920, 0, 3840, 1080) == (0, 540)


def test_norm_to_pixel_clamps_out_of_range():
    assert norm_to_pixel(-0.2, 1.4, 0, 0, 100, 50) == (0, 49)


def test_pixel_to_norm_roundtrip_within_one_pixel():
    left, top, width, height = 0, 0, 1920, 1080
    for x, y in [(0, 0), (10, 10), (960, 540), (1919, 1079)]:
        nx, ny = pixel_to_norm(x, y, left, top, width, height)
        rx, ry = norm_to_pixel(nx, ny, left, top, width, height)
        assert abs(rx - x) <= 1
        assert abs(ry - y) <= 1


def test_pixel_to_norm_handles_negative_origin():
    assert pixel_to_norm(-1920, 0, -1920, 0, 3840, 1080) == (0.0, 0.0)


def test_absolute_units_span_full_range():
    assert norm_to_absolute_units(0.0, 0.0) == (0, 0)
    assert norm_to_absolute_units(1.0, 1.0) == (65535, 65535)
    mid_x, mid_y = norm_to_absolute_units(0.5, 0.5)
    assert abs(mid_x - 32767.5) <= 1
    assert abs(mid_y - 32767.5) <= 1


def test_absolute_units_clamped():
    assert norm_to_absolute_units(-1.0, 2.0) == (0, 65535)


def test_zero_sized_screen_is_rejected():
    with pytest.raises(ValueError):
        norm_to_pixel(0.5, 0.5, 0, 0, 0, 1080)
