"""传输进度文字：大文件传输时状态栏要能看出走了多少。"""

from __future__ import annotations

from viewer.app import human_bytes, progress_text


def test_human_bytes_scales():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1023) == "1023 B"
    assert human_bytes(1024) == "1.0 KB"
    assert human_bytes(1536) == "1.5 KB"
    assert human_bytes(3 * 1024 * 1024) == "3.0 MB"
    assert human_bytes(1024 ** 3) == "1.0 GB"


def test_progress_text_upload():
    assert progress_text("up", "notes.txt", 512, 1024) == "上传 notes.txt 50%（512 B / 1.0 KB）"


def test_progress_text_download_uses_download_wording():
    assert progress_text("down", "report.pdf", 0, 1024).startswith("下载 report.pdf 0%")


def test_progress_text_clamps_to_a_hundred_percent():
    # 分块凑整时收到的字节数可能比 offer 里写的多一点点，别显示 101%。
    assert "100%" in progress_text("up", "a.bin", 1048576, 1048570)


def test_progress_text_handles_zero_total():
    assert progress_text("up", "empty.bin", 0, 0) == "上传 empty.bin 100%（0 B / 0 B）"
