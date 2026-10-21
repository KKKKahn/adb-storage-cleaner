"""File classification, junk whitelist and safe-deletion guards."""

from __future__ import annotations

import os
import posixpath
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from .util import SafetyError

IMAGE_EXT = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".heic",
        ".heif",
        ".webp",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".dng",
        ".arw",
        ".cr2",
        ".nef",
        ".orf",
        ".rw2",
        ".raf",
        ".srw",
        ".pef",
        ".raw",
    }
)
VIDEO_EXT = frozenset(
    {
        ".mp4",
        ".mov",
        ".m4v",
        ".mkv",
        ".avi",
        ".3gp",
        ".3g2",
        ".ts",
        ".mts",
        ".m2ts",
        ".webm",
        ".flv",
        ".wmv",
        ".insv",
        ".lrv",
    }
)
AUDIO_EXT = frozenset(
    {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".amr", ".mid", ".midi"}
)
DOCUMENT_EXT = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".txt",
        ".md",
        ".csv",
        ".tsv",
        ".rtf",
        ".odt",
        ".ods",
        ".epub",
        ".mobi",
        ".azw3",
    }
)
ARCHIVE_EXT = frozenset({".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".iso"})
APK_EXT = frozenset({".apk", ".apks", ".xapk", ".apkm"})

CATEGORY_LABELS = {
    "image": "图片",
    "video": "视频",
    "audio": "音频",
    "document": "文档",
    "archive": "压缩包",
    "apk": "安装包",
    "other": "其他文件",
}

CATEGORY_ORDER = ["image", "video", "audio", "document", "archive", "apk", "other"]


def file_category(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in DOCUMENT_EXT:
        return "document"
    if ext in ARCHIVE_EXT:
        return "archive"
    if ext in APK_EXT:
        return "apk"
    return "other"


def category_totals(entries: Iterable) -> Dict[str, Tuple[int, int]]:
    totals: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    for entry in entries:
        if getattr(entry, "is_dir", False):
            continue
        key = file_category(entry.path)
        totals[key][0] += entry.size
        totals[key][1] += 1
    return {key: (value[0], value[1]) for key, value in totals.items()}


def top_level_dir(path: str) -> str:
    """Group paths into useful top-level buckets for reports."""
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) <= 2:
        return "/sdcard"
    if parts[0] == "sdcard" and parts[1] == "Android" and len(parts) >= 4:
        return "/" + "/".join(parts[:4])
    return "/" + "/".join(parts[:2])


# --------------------------------------------------------------------- safety
SECONDARY_USER_RE = re.compile(r"^/(?:storage/emulated|data/user)/(\d+)(?:/|$)")


def is_secondary_user_path(path: str) -> bool:
    """Privacy space / work profile / app clone live outside user 0."""
    match = SECONDARY_USER_RE.match(path)
    if not match:
        return False
    return match.group(1) != "0"


def normalize(path: str) -> str:
    if "\x00" in path:
        raise SafetyError("路径包含非法字符")
    raw_parts = path.split("/")
    if ".." in raw_parts:
        raise SafetyError(f"拒绝包含 .. 的路径: {path}")
    clean = posixpath.normpath(path)
    return clean.rstrip("/") or "/"


def is_hidden_path(path: str) -> bool:
    parts = [part for part in path.strip("/").split("/") if part]
    return any(part.startswith(".") and part not in (".", "..") for part in parts[1:])


# A directory name is treated as disposable only when the *whole* segment is
# one of these patterns. This prevents matching things like "catalogs".
CACHE_SEGMENT_RE = re.compile(
    r"(?:cache|caches|.*cache.*|.*thumb.*|tmp|temp|logs?)",
    re.IGNORECASE,
)


def is_junk_dir(path: str) -> bool:
    """True only for the small whitelist this project is willing to delete."""
    path = normalize(path)
    if is_secondary_user_path(path):
        return False
    if path == "/sdcard/.thumbnails":
        return True
    if re.fullmatch(r"/sdcard/(?:DCIM|Pictures|Movies|Music|Download)/\.thumbnails", path, re.I):
        return True
    prefix = "/sdcard/Android/data/"
    if path.startswith(prefix):
        parts = path[len(prefix) :].split("/")
        # Must be inside a package directory, never the package root itself.
        if len(parts) < 2:
            return False
        return bool(CACHE_SEGMENT_RE.fullmatch(parts[-1]))
    return False


def assert_cleanable(path: str) -> str:
    """Raise SafetyError unless path is in the junk whitelist and user 0."""
    clean = normalize(path)
    if not clean.startswith("/sdcard/"):
        raise SafetyError(f"拒绝删除 /sdcard 之外的路径: {clean}")
    if is_secondary_user_path(clean):
        raise SafetyError(f"拒绝删除隐私空间/分身的路径: {clean}")
    if not is_junk_dir(clean):
        raise SafetyError(f"不在清理白名单内: {clean}")
    return clean


def is_app_data_path(path: str) -> bool:
    return path.startswith("/sdcard/Android/data/")


def is_auto_deletable_app_file(path: str) -> bool:
    """Inside Android/data only cache-like files may be auto-deleted.

    App-managed originals (gallery originals, databases, DNG bursts, ...) are
    never removed automatically; they are reported for manual review instead.
    OBB and Android/media are app-managed too: an exact duplicate can still be
    required by a different app, so both are protected by default.
    """
    if path.startswith(("/sdcard/Android/obb/", "/sdcard/Android/media/")):
        return False
    if not is_app_data_path(path):
        return True
    parts = path[len("/sdcard/Android/data/") :].split("/")
    if len(parts) < 3:
        return False
    return any(CACHE_SEGMENT_RE.fullmatch(segment) for segment in parts[1:-1])


# ---------------------------------------------------------------- keep policy
SECONDARY_MARKERS = (
    "互传",
    "办公套件",
    "backup",
    "备份",
    "副本",
    "copy",
    "迁移",
    "旧手机",
    "回收站",
    "recycle",
    ".trashed",
)


def keep_score(path: str) -> Tuple[float, int, int, str]:
    """Lower is better; used to pick the copy worth keeping during dedupe."""
    low = path.lower()
    if low.startswith("/sdcard/dcim/"):
        tier = 0
    elif low.startswith("/sdcard/pictures/"):
        tier = 10
    elif low.startswith("/sdcard/movies/"):
        tier = 15
    elif low.startswith(
        ("/sdcard/music/", "/sdcard/documents/", "/sdcard/recordings/", "/sdcard/record/")
    ):
        tier = 20
    elif low.startswith("/sdcard/download/"):
        tier = 40
    elif low.startswith("/sdcard/android/media/"):
        tier = 60
    elif low.startswith("/sdcard/android/data/"):
        tier = 80
    elif low.startswith("/sdcard/vivomediadata/"):
        tier = 95
    else:
        tier = 50

    penalty = 0
    for marker in SECONDARY_MARKERS:
        if marker in low:
            penalty += 25
    if is_hidden_path(path):
        penalty += 30
    if "/cache/" in low or "/.thumbnails/" in low or "/image_cache" in low:
        penalty += 60
    return (tier + penalty, low.count("/"), len(low), path)


def junk_dirs(entries: Iterable) -> List[Tuple[str, int, int]]:
    """Return (path, bytes, file_count) for top-most whitelisted junk dirs."""
    sizes: Dict[str, int] = {}
    for entry in entries:
        if not getattr(entry, "is_dir", False):
            continue
        if is_junk_dir(entry.path):
            sizes[entry.path] = entry.size

    if not sizes:
        return []

    selected: List[str] = []
    for path in sorted(sizes, key=lambda item: (item.count("/"), item)):
        if any(path == parent or path.startswith(parent + "/") for parent in selected):
            continue
        selected.append(path)

    counts = {path: 0 for path in selected}
    for entry in entries:
        if getattr(entry, "is_dir", False):
            continue
        for path in selected:
            if entry.path.startswith(path + "/"):
                counts[path] += 1
                break

    return [(path, sizes[path], counts[path]) for path in selected]
