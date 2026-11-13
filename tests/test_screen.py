import io

import pytest
from PIL import Image

from agent.screen import encode_image, jpeg_size_estimate


def _frame(width: int = 640, height: int = 360) -> Image.Image:
    return Image.new("RGB", (width, height), (30, 90, 150))


def test_encode_returns_decodable_jpeg():
    data, width, height = encode_image(_frame())
    assert data[:2] == b"\xff\xd8"
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.size == (640, 360)
    assert (width, height) == (640, 360)


def test_scale_halves_dimensions():
    data, width, height = encode_image(_frame(1920, 1080), scale=0.5)
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.size == (960, 540)
    assert (width, height) == (960, 540)


def test_scale_above_one_is_clamped():
    data, width, height = encode_image(_frame(800, 600), scale=2.0)
    assert (width, height) == (800, 600)


def test_scale_zero_is_rejected():
    with pytest.raises(ValueError):
        encode_image(_frame(), scale=0.0)


def test_tiny_scale_keeps_one_pixel():
    data, width, height = encode_image(_frame(10, 10), scale=0.01)
    assert (width, height) == (1, 1)


def test_lower_quality_is_smaller():
    high = jpeg_size_estimate(_frame(1280, 720), quality=90)
    low = jpeg_size_estimate(_frame(1280, 720), quality=20)
    assert low < high


def test_quality_out_of_range_clamped():
    data, _, _ = encode_image(_frame(), quality=500)
    assert data[:2] == b"\xff\xd8"
