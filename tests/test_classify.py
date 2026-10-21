import pytest

from adbclean.classify import (
    assert_cleanable,
    file_category,
    is_auto_deletable_app_file,
    is_junk_dir,
    is_secondary_user_path,
    junk_dirs,
    keep_score,
    top_level_dir,
)
from adbclean.scan import Entry
from adbclean.util import SafetyError, human, parse_size


def test_file_category():
    assert file_category("/sdcard/DCIM/a.JPG") == "image"
    assert file_category("/sdcard/DCIM/a.heic") == "image"
    assert file_category("/sdcard/DCIM/a.dng") == "image"
    assert file_category("/sdcard/Movies/a.MOV") == "video"
    assert file_category("/sdcard/Music/a.flac") == "audio"
    assert file_category("/sdcard/Documents/a.pdf") == "document"
    assert file_category("/sdcard/Download/a.zip") == "archive"
    assert file_category("/sdcard/Download/a.apk") == "apk"
    assert file_category("/sdcard/Download/a.bin") == "other"


def test_top_level_dir():
    assert top_level_dir("/sdcard/DCIM/Camera/a.jpg") == "/sdcard/DCIM"
    assert top_level_dir("/sdcard/我的文档/a.txt") == "/sdcard/我的文档"
    assert top_level_dir("/sdcard/Android/data/com.x/files/a") == "/sdcard/Android/data/com.x"
    assert top_level_dir("/sdcard/foo.txt") == "/sdcard"


def test_secondary_users():
    assert not is_secondary_user_path("/storage/emulated/0/DCIM/a.jpg")
    assert is_secondary_user_path("/storage/emulated/666/DCIM/a.jpg")
    assert is_secondary_user_path("/storage/emulated/999/DCIM/a.jpg")
    assert is_secondary_user_path("/data/user/10/com.x/a")
    assert not is_secondary_user_path("/data/user/0/com.x/a")


def test_junk_whitelist_accepts_only_known_caches():
    assert is_junk_dir("/sdcard/DCIM/.thumbnails")
    assert is_junk_dir("/sdcard/Pictures/.thumbnails")
    assert is_junk_dir("/sdcard/.thumbnails")
    assert is_junk_dir("/sdcard/Android/data/com.x/cache")
    assert is_junk_dir("/sdcard/Android/data/com.x/files/cache")
    assert is_junk_dir("/sdcard/Android/data/com.x/files/image_cache_new")
    assert is_junk_dir("/sdcard/Android/data/com.x/files/thumbnails")
    assert is_junk_dir("/sdcard/Android/data/com.x/files/logs")

    assert not is_junk_dir("/sdcard/Android/data/com.x")
    assert not is_junk_dir("/sdcard/Android/data/com.x/files")
    assert not is_junk_dir("/sdcard/Android/data/com.x/files/photos")
    assert not is_junk_dir("/sdcard/Android/data/com.x/files/catalogs")
    assert not is_junk_dir("/sdcard/DCIM/Camera")
    assert not is_junk_dir("/sdcard/Pictures")
    assert not is_junk_dir("/sdcard/Download")


def test_assert_cleanable():
    assert assert_cleanable("/sdcard/Android/data/com.x/cache") == "/sdcard/Android/data/com.x/cache"
    with pytest.raises(SafetyError):
        assert_cleanable("/sdcard/DCIM/Camera")
    with pytest.raises(SafetyError):
        assert_cleanable("/sdcard/Android/data/com.x/files/photos")
    with pytest.raises(SafetyError):
        assert_cleanable("/sdcard/Android/data/com.x")
    with pytest.raises(SafetyError):
        assert_cleanable("/sdcard/Android/data/com.x/../DCIM")
    with pytest.raises(SafetyError):
        assert_cleanable("/")
    with pytest.raises(SafetyError):
        assert_cleanable("/storage/emulated/666/Android/data/com.x/cache")


def test_app_data_file_protection():
    assert is_auto_deletable_app_file("/sdcard/DCIM/Camera/a.jpg")
    assert is_auto_deletable_app_file("/sdcard/Android/data/com.x/cache/a.jpg")
    assert is_auto_deletable_app_file("/sdcard/Android/data/com.x/files/image_cache_new/a.insp")
    assert not is_auto_deletable_app_file("/sdcard/Android/data/com.x/files/galleryOriginal/a.dng")
    assert not is_auto_deletable_app_file("/sdcard/Android/data/com.x/files/a.jpg")
    assert not is_auto_deletable_app_file("/sdcard/Android/data/com.x/files/Insta360OneR/galleryOriginal/a.insp")
    assert not is_auto_deletable_app_file("/sdcard/Android/obb/com.x/main.1.com.x.obb")
    assert not is_auto_deletable_app_file("/sdcard/Android/media/com.x/WhatsApp/Media/a.jpg")


def test_keep_priority():
    dcim = "/sdcard/DCIM/Camera/a.jpg"
    pictures = "/sdcard/Pictures/a.jpg"
    download = "/sdcard/Download/a.jpg"
    app_data = "/sdcard/Android/data/com.x/files/a.jpg"
    hidden = "/sdcard/.hide/a.jpg"
    assert keep_score(dcim) < keep_score(pictures) < keep_score(download) < keep_score(app_data)
    assert keep_score(download) < keep_score(hidden)
    assert keep_score("/sdcard/Pictures/a.jpg") < keep_score("/sdcard/Pictures/互传/a.jpg")


def test_junk_dirs_collapses_nested():
    entries = [
        Entry("/sdcard", 1000, True),
        Entry("/sdcard/Android", 1000, True),
        Entry("/sdcard/Android/data", 1000, True),
        Entry("/sdcard/Android/data/com.x", 1000, True),
        Entry("/sdcard/Android/data/com.x/cache", 600, True),
        Entry("/sdcard/Android/data/com.x/cache/sub", 100, True),
        Entry("/sdcard/Android/data/com.x/cache/a.tmp", 400, False),
        Entry("/sdcard/Android/data/com.x/cache/sub/b.tmp", 100, False),
        Entry("/sdcard/Android/data/com.x/files", 400, True),
        Entry("/sdcard/Android/data/com.x/files/photo.jpg", 400, False),
        Entry("/sdcard/DCIM", 500, True),
        Entry("/sdcard/DCIM/.thumbnails", 500, True),
        Entry("/sdcard/DCIM/.thumbnails/x.thumb", 500, False),
    ]
    rows = {row[0]: row for row in junk_dirs(entries)}
    assert set(rows) == {"/sdcard/Android/data/com.x/cache", "/sdcard/DCIM/.thumbnails"}
    assert rows["/sdcard/Android/data/com.x/cache"][1] == 600
    assert rows["/sdcard/Android/data/com.x/cache"][2] == 2
    assert rows["/sdcard/DCIM/.thumbnails"][1] == 500
    assert rows["/sdcard/DCIM/.thumbnails"][2] == 1


def test_human_and_parse_size():
    assert human(1_000_000_000) == "1.00 GB"
    assert human(1024**3, binary=True) == "1.00 GiB"
    assert parse_size("1M") == 1_000_000
    assert parse_size("1MiB") == 1024**2
    assert parse_size("2.5GB") == 2_500_000_000
    with pytest.raises(ValueError):
        parse_size("abc")
