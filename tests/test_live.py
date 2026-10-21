import os

from klean.live import (
    _write_restore_script,
    find_pairs,
    month_stats,
    total_size,
)
from klean.scan import Entry


def entries():
    return [
        Entry("/sdcard", 0, True),
        Entry("/sdcard/DCIM", 0, True),
        Entry("/sdcard/DCIM/Camera", 0, True),
        Entry("/sdcard/DCIM/Camera/IMG_20260101_120000.jpg", 1000, False),
        Entry("/sdcard/DCIM/Camera/IMG_20260101_120000.mp4", 3000, False),
        Entry("/sdcard/DCIM/Camera/IMG_20260102_120000.JPG", 1000, False),
        Entry("/sdcard/DCIM/Camera/IMG_20260102_120000.mp4", 4000, False),
        Entry("/sdcard/Pictures/Screenshots/Videoshot_20260103_120000.jpg", 100, False),
        Entry("/sdcard/Pictures/Screenshots/Videoshot_20260103_120000.mp4", 5000, False),
        Entry("/sdcard/DCIM/Camera/IMG_20260104_120000_1.jpg", 100, False),
        Entry("/sdcard/DCIM/Camera/IMG_20260104_120000_1.mp4", 9000, False),
        Entry("/sdcard/DCIM/Camera/IMG_20260105_120000.jpg", 100, False),
        Entry("/sdcard/DCIM/Camera/IMG_20260105_120000.mp4", 2000, False),
        Entry("/sdcard/DCIM/Camera/IMG_20260106_120000.jpg", 100, False),
        Entry("/sdcard/DCIM/Camera/IMG_20260106_120000.mp4", 2500, False),
    ]


def test_find_pairs_strict_naming_and_uppercase_jpg():
    pairs, long_pairs, unknown = find_pairs(entries(), durations=None)
    mp4s = {pair.mp4 for pair in pairs}
    assert "/sdcard/DCIM/Camera/IMG_20260101_120000.mp4" in mp4s
    assert "/sdcard/DCIM/Camera/IMG_20260102_120000.mp4" in mp4s  # uppercase JPG
    assert "/sdcard/DCIM/Camera/IMG_20260105_120000.mp4" in mp4s
    assert "/sdcard/DCIM/Camera/IMG_20260106_120000.mp4" in mp4s
    assert not any("Videoshot" in mp4 for mp4 in mp4s)
    assert not any("_1.mp4" in mp4 for mp4 in mp4s)
    assert long_pairs == []
    assert unknown == []


def test_find_pairs_duration_filter_and_unknown():
    durations = {
        "/sdcard/DCIM/Camera/IMG_20260101_120000.mp4": 2000,
        "/sdcard/DCIM/Camera/IMG_20260102_120000.mp4": 9000,
        "/sdcard/DCIM/Camera/IMG_20260105_120000.mp4": 1500,
    }
    pairs, long_pairs, unknown = find_pairs(entries(), durations, max_duration_ms=5000)
    assert {pair.mp4 for pair in pairs} == {
        "/sdcard/DCIM/Camera/IMG_20260101_120000.mp4",
        "/sdcard/DCIM/Camera/IMG_20260105_120000.mp4",
    }
    assert {pair.mp4 for pair in long_pairs} == {"/sdcard/DCIM/Camera/IMG_20260102_120000.mp4"}
    assert {pair.mp4 for pair in unknown} == {"/sdcard/DCIM/Camera/IMG_20260106_120000.mp4"}


def test_find_pairs_include_unknown():
    durations = {
        "/sdcard/DCIM/Camera/IMG_20260101_120000.mp4": 2000,
    }
    pairs, _, unknown = find_pairs(
        entries(), durations, max_duration_ms=5000, include_unknown=True
    )
    assert any(pair.mp4.endswith("IMG_20260106_120000.mp4") for pair in pairs)
    assert any(pair.duration_ms is None for pair in pairs)
    assert len(unknown) == 3  # 02, 05 and 06 have no duration in this fixture


def test_month_stats_and_total():
    pairs, _, _ = find_pairs(entries(), durations=None)
    stats = month_stats(pairs)
    assert stats == [("2026-01", 4, 3000 + 4000 + 2000 + 2500)]
    assert total_size(pairs) == 11500


def test_restore_script(tmp_path):
    pairs, _, _ = find_pairs(entries(), durations=None)
    script = _write_restore_script(str(tmp_path), pairs[:1])
    assert os.path.exists(script)
    with open(script, encoding="utf-8") as handle:
        content = handle.read()
    assert 'ADB="${ADB:-adb}"' in content
    assert " push " in content
    assert "MEDIA_SCANNER_SCAN_FILE" in content
    assert "IMG_20260101_120000.mp4" in content
