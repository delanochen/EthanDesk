import pytest

from common.protocol import (
    BinaryPairer,
    ProtocolError,
    decode,
    encode,
    is_bin_header,
    make_bin_header,
)


def test_encode_decode_roundtrip():
    msg = {"t": "input", "ev": "mouse_move", "x": 0.5, "y": 0.25}
    assert decode(encode(msg)) == msg


def test_decode_accepts_bytes():
    assert decode(encode({"t": "ping"}).encode("utf-8")) == {"t": "ping"}


def test_decode_rejects_invalid_json():
    with pytest.raises(ProtocolError):
        decode("{not json")


def test_decode_unknown_type_returns_none():
    assert decode(encode({"t": "no_such_type"})) is None


def test_decode_missing_type_returns_none():
    assert decode(encode({"x": 1})) is None


def test_decode_non_object_returns_none():
    assert decode(encode([1, 2, 3])) is None


def test_bin_header_shape():
    header = make_bin_header("frame", seq=1, size=10)
    assert header["t"] == "bin"
    assert header["kind"] == "frame"
    assert header["seq"] == 1
    assert header["size"] == 10
    assert is_bin_header(header) is True


def test_is_bin_header_rejects_other_messages():
    assert is_bin_header({"t": "input"}) is False
    assert is_bin_header(None) is False


def test_pairer_requires_header_first():
    pairer = BinaryPairer()
    with pytest.raises(ProtocolError):
        pairer.attach(b"abc")


def test_pairer_pairs_header_with_payload():
    pairer = BinaryPairer()
    header = make_bin_header("file", id="f1", seq=0, size=3)
    pairer.header(header)
    assert pairer.pending is True
    assert pairer.attach(b"abc") == (header, b"abc")
    assert pairer.pending is False


def test_pairer_rejects_non_bin_header():
    pairer = BinaryPairer()
    with pytest.raises(ProtocolError):
        pairer.header({"t": "input"})
    assert pairer.pending is False
