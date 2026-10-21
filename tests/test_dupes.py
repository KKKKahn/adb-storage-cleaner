from adbclean.dupes import (
    build_groups,
    candidate_groups,
    parse_hash_line,
)
from adbclean.scan import Entry


def test_candidate_groups_filters_small_and_unique():
    entries = [
        Entry("/sdcard/a.bin", 2_000_000, False),
        Entry("/sdcard/b.bin", 2_000_000, False),
        Entry("/sdcard/c.bin", 3_000_000, False),
        Entry("/sdcard/small.bin", 10, False),
        Entry("/sdcard/small2.bin", 10, False),
        Entry("/sdcard/dir", 2_000_000, True),
    ]
    groups = candidate_groups(entries, 1_000_000)
    assert groups == {2_000_000: ["/sdcard/a.bin", "/sdcard/b.bin"]}


def test_parse_hash_line():
    digest = "d41d8cd98f00b204e9800998ecf8427e"
    assert parse_hash_line(f"{digest}  /sdcard/a b.jpg") == (digest, "/sdcard/a b.jpg")
    assert parse_hash_line(f"{digest} /sdcard/a.jpg") == (digest, "/sdcard/a.jpg")
    assert parse_hash_line("\\" + digest + "  /sdcard/weird\\nname.jpg") is None
    assert parse_hash_line("garbage") is None


def test_build_groups_keeps_main_copy_and_protects_app_data():
    hashes = {
        "/sdcard/DCIM/Camera/a.jpg": "aaa",
        "/sdcard/Download/a.jpg": "aaa",
        "/sdcard/Android/data/com.x/files/galleryOriginal/a.jpg": "aaa",
    }
    sizes = {path: 1000 for path in hashes}
    groups = build_groups(hashes, sizes)
    assert len(groups) == 1
    group = groups[0]
    assert group.keeper == "/sdcard/DCIM/Camera/a.jpg"
    assert group.deletable == ["/sdcard/Download/a.jpg"]
    assert group.blocked == ["/sdcard/Android/data/com.x/files/galleryOriginal/a.jpg"]
    assert group.savings == 1000


def test_build_groups_include_app_data():
    hashes = {
        "/sdcard/DCIM/Camera/a.jpg": "aaa",
        "/sdcard/Android/data/com.x/files/galleryOriginal/a.jpg": "aaa",
    }
    sizes = {path: 1000 for path in hashes}
    groups = build_groups(hashes, sizes, include_app_data=True)
    assert groups[0].deletable == ["/sdcard/Android/data/com.x/files/galleryOriginal/a.jpg"]
    assert groups[0].blocked == []


def test_build_groups_prefers_visible_copy_over_hidden():
    hashes = {
        "/sdcard/vivoMediaData/.hide/Gallery/a.jpg": "aaa",
        "/sdcard/Download/a.jpg": "aaa",
    }
    sizes = {path: 1000 for path in hashes}
    groups = build_groups(hashes, sizes)
    assert groups[0].keeper == "/sdcard/Download/a.jpg"
    assert groups[0].deletable == ["/sdcard/vivoMediaData/.hide/Gallery/a.jpg"]


def test_build_groups_protects_obb_and_android_media():
    hashes = {
        "/sdcard/DCIM/Camera/a.obb": "aaa",
        "/sdcard/Android/obb/com.x/a.obb": "aaa",
        "/sdcard/Android/media/com.x/a.obb": "aaa",
    }
    sizes = {path: 1000 for path in hashes}
    groups = build_groups(hashes, sizes)
    assert groups[0].deletable == []
    assert set(groups[0].blocked) == {
        "/sdcard/Android/obb/com.x/a.obb",
        "/sdcard/Android/media/com.x/a.obb",
    }


def test_build_groups_cache_copy_is_deletable():
    hashes = {
        "/sdcard/DCIM/Camera/a.jpg": "aaa",
        "/sdcard/Android/data/com.x/cache/a.jpg": "aaa",
    }
    sizes = {path: 1000 for path in hashes}
    groups = build_groups(hashes, sizes)
    assert groups[0].keeper == "/sdcard/DCIM/Camera/a.jpg"
    assert groups[0].deletable == ["/sdcard/Android/data/com.x/cache/a.jpg"]
