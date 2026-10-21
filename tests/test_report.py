
from adbclean.report import build, render_html, render_markdown
from adbclean.scan import Entry, Scan


class FakeADB:
    serial = "SERIAL123456"

    def props(self):
        return {
            "ro.product.brand": "Test",
            "ro.product.model": "Phone",
            "ro.build.version.release": "16",
            "ro.build.version.sdk": "36",
        }

    def df(self, path):
        return {
            "total_kb": 100 * 1024 * 1024,
            "used_kb": 50 * 1024 * 1024,
            "avail_kb": 50 * 1024 * 1024,
            "use_percent": 50,
        }

    def users(self):
        return [0]

    def content_query(self, uri, projection, timeout=180):
        return ""  # no MediaStore -> report still finds pairs by name


def make_scan():
    entries = [
        Entry("/sdcard", 12_000_000, True),
        Entry("/sdcard/DCIM", 10_000_000, True),
        Entry("/sdcard/DCIM/Camera", 10_000_000, True),
        Entry("/sdcard/DCIM/Camera/IMG_20260101_120000.jpg", 2_000_000, False),
        Entry("/sdcard/DCIM/Camera/IMG_20260101_120000.mp4", 3_000_000, False),
        Entry("/sdcard/DCIM/Camera/photo.jpg", 3_000_000, False),
        Entry("/sdcard/Movies/clip.mp4", 5_000_000, False),
        Entry("/sdcard/Android", 2_000_000, True),
        Entry("/sdcard/Android/data", 2_000_000, True),
        Entry("/sdcard/Android/data/com.x", 2_000_000, True),
        Entry("/sdcard/Android/data/com.x/cache", 1_000_000, True),
        Entry("/sdcard/Android/data/com.x/cache/a.tmp", 1_000_000, False),
        Entry("/sdcard/Android/data/com.x/files", 1_000_000, True),
        Entry("/sdcard/Android/data/com.x/files/b.bin", 1_000_000, False),
    ]
    scan = Scan(serial="SERIAL123456", root="/sdcard", scanned_at="2026-10-21T12:00:00+08:00", entries=entries)
    return scan


def test_build_and_markdown():
    data = build(make_scan(), FakeADB())
    assert data.junk_total == 1_000_000
    assert len(data.live_pairs) == 1
    markdown = render_markdown(data)
    assert "# 安卓存储审计报告" in markdown
    assert "动态照片伴生视频" in markdown
    assert "Test Phone" in markdown
    assert "SERI***56" in markdown
    assert "可安全清理项" in markdown


def test_html_escapes_and_contains_tables():
    data = build(make_scan(), FakeADB())
    page = render_html(data)
    assert page.startswith("<!doctype html>")
    assert "Test Phone" in page
    assert "<table>" in page
    assert "&lt;" not in page or "script" not in page


def test_large_files_threshold():
    scan = make_scan()
    scan.entries.append(Entry("/sdcard/big.bin", 300_000_000, False))
    data = build(scan, FakeADB())
    assert data.large_files == [("/sdcard/big.bin", 300_000_000)]
