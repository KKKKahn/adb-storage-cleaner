"""Regular (non-live) video discovery and compression for Klean's video track."""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .adb import ADB
from .compress import (
    SAFE_COLOR_PRIMARIES,
    SAFE_COLOR_RANGES,
    SAFE_COLOR_SPACES,
    SAFE_COLOR_TRCS,
    SAFE_PIX_FMTS,
    CompressError,
    HdrUnsupported,
    NoBenefit,
    UnsupportedFormat,
    VideoInfo,
    _run,
    _run_with_progress,
    hdr_reason,
    probe_video,
    verify_decodable,
)
from .live import LIVE_RE

VIDEO_MIME = "video/mp4"
VIDEO_EXT = ".mp4"
PROBABLE_LIVE_MAX_MS = 5000
VIDEO_WORKERS = 2
VIDEO_TIMEOUT = 7200
VIDEO_BATCH_SUGGESTION = 6_000_000_000


@dataclass
class VideoItem:
    media_id: str
    path: str
    size: int
    duration_ms: Optional[int]
    width: int
    height: int
    date: str
    time: str
    mime: str = VIDEO_MIME

    @property
    def month(self) -> str:
        return self.date[:7]

    @property
    def album(self) -> str:
        return os.path.dirname(self.path)


@dataclass(frozen=True)
class VideoPreset:
    key: str
    label: str
    crf: int
    quality: int


VIDEO_PRESETS = {
    "balanced": VideoPreset("balanced", "推荐 CRF20", 20, 60),
    "small": VideoPreset("small", "小体积 CRF23", 23, 50),
}
VIDEO_PRESET_ORDER = ("balanced", "small")


@dataclass
class VideoCompressResult:
    original_size: int
    compressed_size: int
    codec: str
    crf: int
    quality: Optional[int]
    audio_reencoded: bool
    speed_used: str
    elapsed_ms: int


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _media_path(value: str) -> str:
    for prefix in ("/storage/emulated/0", "/storage/self/primary"):
        if value.startswith(prefix):
            return "/sdcard" + value[len(prefix) :]
    return value


def parse_media_rows(text: str) -> List[Dict[str, str]]:
    """Parse `content query` rows into dicts of MediaStore fields."""
    rows: List[Dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("Row:"):
            continue
        line = re.sub(r"^Row:\s*\d+\s*", "", line)
        fields: Dict[str, str] = {}
        for match in re.finditer(
            r"(?:(?<=^)|(?<=, ))([A-Za-z_][A-Za-z0-9_]*)=(.*?)(?=, [A-Za-z_][A-Za-z0-9_]*=|$)",
            line,
        ):
            fields[match.group(1)] = match.group(2)
        if fields:
            rows.append(fields)
    return rows


def _media_date(seconds: int) -> Tuple[str, str]:
    if seconds <= 0:
        return ("1970-01-01", "00:00:00")
    value = dt.datetime.fromtimestamp(seconds)
    return (value.strftime("%Y-%m-%d"), value.strftime("%H:%M:%S"))


def load_videos(
    adb: ADB,
    live_paths: Optional[Iterable[str]] = None,
    size_map: Optional[Dict[str, int]] = None,
) -> List[VideoItem]:
    projection = "_id:_data:duration:width:height:_size:date_added:mime_type"
    text = adb.content_query(
        "content://media/external/video/media", projection, timeout=240
    )
    live = set(live_paths or ())
    sizes = size_map or {}
    videos: List[VideoItem] = []
    seen: set = set()
    for fields in parse_media_rows(text):
        raw_path = fields.get("_data", "")
        path = _media_path(raw_path)
        if not path.lower().endswith(VIDEO_EXT):
            continue
        mime = fields.get("mime_type", "") or VIDEO_MIME
        if path in live or path in seen:
            continue
        if path.startswith("/sdcard/Android/") or "/.thumbnails/" in path:
            continue
        duration = _as_int(fields.get("duration"), 0)
        name = os.path.basename(path)
        # A short IMG_*.mp4 without a JPG is usually an orphan live-photo
        # companion; do not silently treat it as a normal video.
        if LIVE_RE.match(name) and (not duration or duration <= PROBABLE_LIVE_MAX_MS):
            continue
        size = _as_int(fields.get("_size"), 0) or sizes.get(path, 0)
        if size <= 0:
            continue
        media_id = fields.get("_id", "").strip()
        if not media_id.isdigit():
            continue
        date, clock = _media_date(_as_int(fields.get("date_added"), 0))
        if date == "1970-01-01":
            match = re.search(r"(20\d{6})_(\d{6})", name)
            if match:
                raw = match.group(1)
                date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
                raw_time = match.group(2)
                clock = f"{raw_time[:2]}:{raw_time[2:4]}:{raw_time[4:6]}"
        videos.append(
            VideoItem(
                media_id=media_id,
                path=path,
                size=size,
                duration_ms=duration or None,
                width=_as_int(fields.get("width"), 0),
                height=_as_int(fields.get("height"), 0),
                date=date,
                time=clock,
                mime=mime,
            )
        )
        seen.add(path)
    videos.sort(key=lambda item: (item.date, item.time), reverse=True)
    return videos


def dates_for(videos: Iterable[VideoItem]) -> List[Dict]:
    buckets: Dict[str, List[VideoItem]] = {}
    for video in videos:
        buckets.setdefault(video.date, []).append(video)
    return [
        {
            "date": date,
            "count": len(items),
            "bytes": sum(item.size for item in items),
        }
        for date, items in sorted(buckets.items(), reverse=True)
    ]


def months_for(videos: Iterable[VideoItem]) -> List[Dict]:
    buckets: Dict[str, List[VideoItem]] = {}
    for video in videos:
        buckets.setdefault(video.month, []).append(video)
    return [
        {
            "month": month,
            "count": len(items),
            "bytes": sum(item.size for item in items),
        }
        for month, items in sorted(buckets.items(), reverse=True)
    ]


def albums_for(videos: Iterable[VideoItem]) -> List[Dict]:
    buckets: Dict[str, List[VideoItem]] = {}
    for video in videos:
        buckets.setdefault(video.album, []).append(video)
    rows = []
    for album, items in buckets.items():
        parts = [part for part in album.split("/") if part]
        rows.append(
            {
                "album": album,
                "name": parts[-1] if parts else album,
                "parent": "/".join(parts[:-1]) or "/",
                "count": len(items),
                "bytes": sum(item.size for item in items),
            }
        )
    rows.sort(key=lambda row: (-row["bytes"], row["album"]))
    return rows


@lru_cache(maxsize=8)
def _encoder_list(ffmpeg: str) -> str:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.decode("utf-8", "replace")


def _hardware_encoder(info: VideoInfo, ffmpeg: str) -> str:
    encoders = _encoder_list(ffmpeg)
    if info.codec_name == "h264" and "h264_videotoolbox" in encoders:
        return "h264_videotoolbox"
    if info.codec_name == "hevc" and "hevc_videotoolbox" in encoders:
        return "hevc_videotoolbox"
    return ""


def _software_encoder(info: VideoInfo, ffmpeg: str) -> str:
    encoders = _encoder_list(ffmpeg)
    if info.codec_name == "h264" and "libx264" in encoders:
        return "libx264"
    if info.codec_name == "hevc" and "libx265" in encoders:
        return "libx265"
    return ""


def _build_command(
    src: str,
    dst: str,
    preset: VideoPreset,
    info: VideoInfo,
    ffmpeg: str,
) -> Tuple[List[str], str, Optional[int], bool]:
    if info.codec_name not in ("h264", "hevc"):
        raise UnsupportedFormat(f"暂不支持 {info.codec_name or '未知'} 编码的视频")
    if info.pix_fmt not in SAFE_PIX_FMTS:
        raise UnsupportedFormat(f"暂不支持像素格式 {info.pix_fmt}")
    reason = hdr_reason(info)
    if reason:
        raise HdrUnsupported(reason)
    hardware = _hardware_encoder(info, ffmpeg)
    software = _software_encoder(info, ffmpeg)
    encoder = hardware or software
    if not encoder:
        raise UnsupportedFormat(f"没有可用的 {info.codec_name} 编码器")
    use_hardware = bool(hardware)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        src,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-sn",
        "-dn",
    ]
    if use_hardware:
        command.extend(["-c:v", encoder, "-q:v", str(preset.quality)])
    else:
        command.extend(
            ["-c:v", encoder, "-preset", "veryfast", "-crf", str(preset.crf)]
        )
    if info.codec_name == "h264":
        command.extend(["-profile:v", "high"])
    else:
        command.extend(["-tag:v", "hvc1"])
    command.extend(["-pix_fmt", info.pix_fmt])
    for flag, value, allowed in (
        ("-color_range", info.color_range, SAFE_COLOR_RANGES),
        ("-colorspace", info.color_space, SAFE_COLOR_SPACES),
        ("-color_primaries", info.color_primaries, SAFE_COLOR_PRIMARIES),
        ("-color_trc", info.color_transfer, SAFE_COLOR_TRCS),
    ):
        if value and value in allowed:
            command.extend([flag, value])
    if info.has_audio:
        command.extend(["-c:a", "copy"])
    command.extend(
        [
            "-map_metadata",
            "0",
            "-vsync",
            "0",
            "-brand",
            "mp42",
            "-movflags",
            "+faststart+use_metadata_tags",
            dst,
        ]
    )
    return command, encoder, preset.quality if use_hardware else None, use_hardware


def compress_regular_video(
    src: str,
    dst: str,
    preset: VideoPreset,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    timeout: int = VIDEO_TIMEOUT,
    on_progress: Optional[Callable[[float], None]] = None,
) -> VideoCompressResult:
    import time

    started = time.monotonic()
    info = probe_video(src, ffprobe)
    command, encoder, quality, use_hardware = _build_command(
        src, dst, preset, info, ffmpeg
    )
    progress_command = list(command)
    if on_progress is not None and info.duration > 0:
        progress_command[1:1] = ["-progress", "pipe:1", "-nostats"]
        result = _run_with_progress(
            progress_command, timeout, float(info.duration), on_progress
        )
    else:
        result = _run(progress_command, timeout)

    audio_reencoded = False
    if result.returncode != 0 and info.has_audio:
        fallback = list(command)
        try:
            index = fallback.index("-c:a")
            fallback[index + 1] = "aac"
            fallback[index + 2 : index + 2] = ["-b:a", "128k"]
        except ValueError:
            fallback = command
        result = _run(fallback, timeout)
        audio_reencoded = result.returncode == 0
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise CompressError(f"视频编码失败: {stderr or '未知错误'}")

    if info.duration > 120:
        _run([ffmpeg, "-v", "error", "-i", dst, "-t", "5", "-f", "null", "-"], min(timeout, 120))
    else:
        verify_decodable(dst, ffmpeg, timeout)
    original_size = os.path.getsize(src)
    compressed_size = os.path.getsize(dst)
    if compressed_size >= original_size:
        raise NoBenefit(
            f"压缩后没有变小（{original_size} → {compressed_size} 字节）"
        )
    return VideoCompressResult(
        original_size=original_size,
        compressed_size=compressed_size,
        codec=info.codec_name,
        crf=preset.crf,
        quality=quality,
        audio_reencoded=audio_reencoded,
        speed_used="hardware" if use_hardware else "veryfast",
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
