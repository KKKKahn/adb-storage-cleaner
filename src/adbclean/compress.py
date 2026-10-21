"""Compress vivo live-photo MP4 companions while preserving the vivo uuid box.

The still JPG is never touched. Only the short companion MP4 is re-encoded and
the exact ``vivoMediaExtInfo`` uuid box is copied from the original file, byte
for byte, to the end of the output. Without that box the vivo gallery stops
treating the pair as a live photo.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

VIVO_UUID_USER_TYPE = b"vivoMediaExtInfo"

HDR_TRANSFERS = frozenset({"arib-std-b67", "smpte2084"})
HDR_PRIMARIES = frozenset({"bt2020"})
SAFE_PIX_FMTS = frozenset({"yuv420p", "yuvj420p"})
SAFE_COLOR_RANGES = frozenset({"tv", "pc", "mpeg", "jpeg", "unspecified", ""})
SAFE_COLOR_SPACES = frozenset(
    {
        "bt709",
        "bt470bg",
        "bt470m",
        "smpte170m",
        "smpte240m",
        "smpte432",
        "bt2020nc",
        "bt2020c",
        "unspecified",
        "",
    }
)
SAFE_COLOR_PRIMARIES = frozenset(
    {
        "bt709",
        "bt470m",
        "bt470bg",
        "smpte170m",
        "smpte240m",
        "smpte431",
        "smpte432",
        "bt2020",
        "unspecified",
        "",
    }
)
SAFE_COLOR_TRCS = frozenset(
    {
        "bt709",
        "gamma22",
        "gamma28",
        "smpte170m",
        "smpte240m",
        "linear",
        "iec61966-2-1",
        "iec61966-2-4",
        "bt2020-10",
        "bt2020-12",
        "unspecified",
        "",
    }
)


class CompressError(RuntimeError):
    """Compression could not be completed."""


class HdrUnsupported(CompressError):
    """10-bit / HDR source: deliberately skipped to avoid colour damage."""


class UnsupportedFormat(CompressError):
    """Pixel format or stream layout this tool refuses to touch."""


class NoVivoUuid(CompressError):
    """Source has no vivoMediaExtInfo box; not a verified vivo live photo."""


class NoBenefit(CompressError):
    """The re-encoded file is not smaller than the original."""


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    crf: Optional[int]
    mode: str
    description: str


PRESETS: Dict[str, Preset] = {
    "visually_lossless": Preset(
        "visually_lossless", "视觉无损", 18, "encode", "SSIM 约 0.99，体积约 56–59%"
    ),
    "balanced": Preset("balanced", "推荐", 20, "encode", "默认档，体积约 43–45%"),
    "small": Preset("small", "小体积", 23, "encode", "体积约 29–30%，建议先人眼确认"),
    "lossless": Preset(
        "lossless", "无损重封装", None, "copy", "像素完全不变，但体积几乎不会减小"
    ),
}
PRESET_ORDER = ["visually_lossless", "balanced", "small", "lossless"]

SPEED_LABELS = {
    "hardware": "硬件加速",
    "veryfast": "快",
    "medium": "均衡",
    "slow": "最小体积",
}
SPEED_ORDER = ["hardware", "veryfast", "medium", "slow"]

CUSTOM_CRF_MIN = 1
CUSTOM_CRF_MAX = 51
UI_CRF_MIN = 14
UI_CRF_MAX = 30


def resolve_speed(value: str) -> str:
    if value not in SPEED_LABELS:
        raise ValueError(f"未知编码速度: {value}")
    return value


def hardware_quality(crf: Optional[int]) -> int:
    """Map the software CRF tier to a VideoToolbox quality level."""
    if crf is None:
        return 60
    if crf <= 18:
        return 65
    if crf <= 21:
        return 60
    if crf <= 24:
        return 50
    return 45


def detect_hardware_encoder(ffmpeg: str = "ffmpeg", timeout: int = 15) -> str:
    """Return a supported Apple hardware H.264 encoder, or an empty string."""
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return "h264_videotoolbox" if b"h264_videotoolbox" in result.stdout else ""


def resolve_preset(key: str, crf: Optional[int] = None) -> Preset:
    if key == "custom":
        if crf is None:
            raise ValueError("自定义档位必须提供 CRF")
        if not CUSTOM_CRF_MIN <= int(crf) <= CUSTOM_CRF_MAX:
            raise ValueError(f"CRF 必须在 {CUSTOM_CRF_MIN}–{CUSTOM_CRF_MAX} 之间")
        return Preset(
            "custom",
            f"自定义 CRF {int(crf)}",
            int(crf),
            "encode",
            "自定义质量/体积权衡",
        )
    preset = PRESETS.get(key)
    if preset is None:
        raise ValueError(f"未知压缩档位: {key}")
    if crf is not None and preset.mode != "encode":
        raise ValueError(f"档位 {key} 不接受自定义 CRF")
    return preset


@dataclass
class VideoInfo:
    duration: float
    size: int
    width: int
    height: int
    display_width: int
    display_height: int
    codec_name: str
    pix_fmt: str
    color_range: str
    color_space: str
    color_transfer: str
    color_primaries: str
    rotation: int
    bit_depth: int
    has_video: bool
    has_audio: bool
    audio_codec: str
    time_base: str
    format_tags: Dict[str, str] = field(default_factory=dict)
    video_tags: Dict[str, str] = field(default_factory=dict)
    audio_tags: Dict[str, str] = field(default_factory=dict)

    @property
    def display_size(self) -> str:
        return f"{self.display_width}x{self.display_height}"


@dataclass
class CompressResult:
    original_size: int
    compressed_size: int
    saved_bytes: int
    ratio: float
    ssim: Optional[float]
    duration: float
    uuid_preserved: bool
    audio_reencoded: bool = False


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stream_tags(stream: dict) -> Dict[str, str]:
    tags = stream.get("tags") or {}
    return {str(key).lower(): str(value) for key, value in tags.items()}


def _rotation(stream: dict) -> int:
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            try:
                return int(round(float(side_data["rotation"])))
            except (TypeError, ValueError):
                continue
    tags = _stream_tags(stream)
    if "rotate" in tags:
        try:
            return int(round(float(tags["rotate"])))
        except ValueError:
            pass
    return 0


def _bit_depth(pix_fmt: str) -> int:
    match = re.search(r"(?:p|le|be)?(\d{2})(?:le|be)?$", pix_fmt or "")
    if match:
        depth = int(match.group(1))
        if depth in (9, 10, 12, 14, 16):
            return depth
    return 8


def parse_probe_json(payload: Dict) -> VideoInfo:
    streams = payload.get("streams") or []
    fmt = payload.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise UnsupportedFormat("文件里没有视频流")

    width = _as_int(video.get("width"))
    height = _as_int(video.get("height"))
    rotation = _rotation(video) % 360
    display_width, display_height = width, height
    if rotation in (90, 270):
        display_width, display_height = height, width

    duration = _as_float(video.get("duration")) or _as_float(fmt.get("duration"))
    format_tags = {
        str(key).lower(): str(value) for key, value in (fmt.get("tags") or {}).items()
    }
    pix_fmt = str(video.get("pix_fmt") or "")
    return VideoInfo(
        duration=duration,
        size=_as_int(fmt.get("size")),
        width=width,
        height=height,
        display_width=display_width,
        display_height=display_height,
        codec_name=str(video.get("codec_name") or ""),
        pix_fmt=pix_fmt,
        color_range=str(video.get("color_range") or ""),
        color_space=str(video.get("color_space") or ""),
        color_transfer=str(video.get("color_transfer") or ""),
        color_primaries=str(video.get("color_primaries") or ""),
        rotation=rotation,
        bit_depth=_bit_depth(pix_fmt),
        has_video=True,
        has_audio=audio is not None,
        audio_codec=str((audio or {}).get("codec_name") or ""),
        time_base=str(video.get("time_base") or ""),
        format_tags=format_tags,
        video_tags=_stream_tags(video),
        audio_tags=_stream_tags(audio or {}),
    )


def probe_video(path: str, ffprobe: str = "ffprobe", timeout: int = 60) -> VideoInfo:
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise CompressError(f"找不到 ffprobe: {ffprobe}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CompressError("ffprobe 超时") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise CompressError(f"ffprobe 失败: {stderr or '无法解析该文件'}")
    try:
        payload = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
    except ValueError as exc:
        raise CompressError("ffprobe 返回了无效 JSON") from exc
    return parse_probe_json(payload)


def hdr_reason(info: VideoInfo) -> Optional[str]:
    if info.bit_depth > 8:
        return f"{info.bit_depth}-bit 视频（可能是 HDR），为避免偏色默认跳过"
    if info.color_transfer in HDR_TRANSFERS:
        return f"HDR 传输特性 {info.color_transfer}，默认跳过"
    if info.color_primaries in HDR_PRIMARIES:
        return f"BT.2020 色彩空间（{info.color_primaries}），默认跳过"
    return None


def _safe(value: str, allowed: frozenset, fallback: str = "") -> str:
    return value if value in allowed else fallback


def _level(info: VideoInfo) -> Optional[str]:
    longest = max(info.width, info.height, info.display_width, info.display_height)
    if longest <= 1920:
        return "4.0"
    return None


def _time_base(info: VideoInfo) -> str:
    match = re.fullmatch(r"1/(\d{1,9})", info.time_base or "")
    if match and 1000 <= int(match.group(1)) <= 10_000_000:
        return info.time_base
    return "1/90000"


def build_encode_command(
    src: str,
    dst: str,
    preset: Preset,
    info: VideoInfo,
    ffmpeg: str = "ffmpeg",
    speed: str = "slow",
) -> List[str]:
    """Build the ffmpeg argv. Pure function, no side effects."""
    speed = resolve_speed(speed)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        src,
        "-map",
        "0",
    ]
    if preset.mode == "copy":
        command.extend(["-c", "copy"])
    else:
        if speed == "hardware":
            command.extend(
                ["-c:v", "h264_videotoolbox", "-q:v", str(hardware_quality(preset.crf))]
            )
        else:
            command.extend(["-c:v", "libx264", "-preset", speed, "-crf", str(preset.crf)])
        pix_fmt = info.pix_fmt if info.pix_fmt in SAFE_PIX_FMTS else "yuv420p"
        command.extend(["-pix_fmt", pix_fmt])
        command.extend(["-profile:v", "high"])
        level = _level(info)
        if level:
            command.extend(["-level", level])
        color_range = _safe(info.color_range, SAFE_COLOR_RANGES)
        color_space = _safe(info.color_space, SAFE_COLOR_SPACES)
        color_primaries = _safe(info.color_primaries, SAFE_COLOR_PRIMARIES)
        color_trc = _safe(info.color_transfer, SAFE_COLOR_TRCS)
        if color_range:
            command.extend(["-color_range", color_range])
        if color_space:
            command.extend(["-colorspace", color_space])
        if color_primaries:
            command.extend(["-color_primaries", color_primaries])
        if color_trc:
            command.extend(["-color_trc", color_trc])
    if info.has_audio:
        command.extend(["-c:a", "copy"])
    command.extend(["-dn", "-sn", "-map_metadata", "0"])

    android_version = info.format_tags.get("com.android.version", "")
    if android_version:
        command.extend(["-metadata", f"com.android.version={android_version}"])
    video_handler = info.video_tags.get("handler_name", "")
    if video_handler:
        command.extend(["-metadata:s:v:0", f"handler_name={video_handler}"])
    audio_handler = info.audio_tags.get("handler_name", "")
    if audio_handler:
        command.extend(["-metadata:s:a:0", f"handler_name={audio_handler}"])

    if preset.mode != "copy":
        command.extend(["-vsync", "0", "-enc_time_base", _time_base(info)])
    command.extend(
        [
            "-brand",
            "mp42",
            "-movflags",
            "+faststart+use_metadata_tags",
            dst,
        ]
    )
    return command


def _run(command: List[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise CompressError(f"找不到 ffmpeg: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CompressError(f"编码超时（{timeout}s）") from exc


def encode_video(
    src: str,
    dst: str,
    preset: Preset,
    info: VideoInfo,
    ffmpeg: str = "ffmpeg",
    timeout: int = 900,
    speed: str = "slow",
) -> bool:
    """Encode src to dst. Returns True when audio had to be re-encoded."""
    command = build_encode_command(src, dst, preset, info, ffmpeg, speed)
    result = _run(command, timeout)
    if result.returncode == 0:
        return False
    stderr = result.stderr.decode("utf-8", "replace").strip()
    audio_copy_failed = (
        info.has_audio
        and preset.mode != "copy"
        and "-c:a" in command
        and _looks_like_audio_failure(stderr)
    )
    if not audio_copy_failed:
        raise CompressError(f"ffmpeg 编码失败: {stderr or '未知错误'}")
    fallback = list(command)
    index = fallback.index("-c:a")
    fallback[index + 1] = "aac"
    fallback[index + 2 : index + 2] = ["-b:a", "96k"]
    result = _run(fallback, timeout)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise CompressError(f"ffmpeg 编码失败（音频回退也失败）: {stderr or '未知错误'}")
    return True


def _looks_like_audio_failure(stderr: str) -> bool:
    text = stderr.lower()
    markers = ("audio", "codec", "encoder", "stream")
    return any(marker in text for marker in markers)


def measure_ssim(
    original: str,
    compressed: str,
    ffmpeg: str = "ffmpeg",
    timeout: int = 300,
) -> Optional[float]:
    result = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            original,
            "-i",
            compressed,
            "-lavfi",
            "[0:v][1:v]ssim",
            "-shortest",
            "-f",
            "null",
            "-",
        ],
        timeout,
    )
    text = result.stderr.decode("utf-8", "replace")
    match = re.search(r"All:\s*([0-9.]+)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


BENIGN_DECODE_WARNINGS = ("non monotonically increasing dts", "non-monotonic dts")


def verify_decodable(path: str, ffmpeg: str = "ffmpeg", timeout: int = 300) -> None:
    result = _run(
        [ffmpeg, "-v", "error", "-i", path, "-f", "null", "-"],
        timeout,
    )
    stderr = result.stderr.decode("utf-8", "replace").strip()
    # The null muxer complains about duplicate DTS on VFR live-photo clips even
    # though decoding succeeds; only surface other errors.
    lines = [
        line
        for line in stderr.splitlines()
        if line.strip() and not any(marker in line.lower() for marker in BENIGN_DECODE_WARNINGS)
    ]
    if result.returncode != 0 or lines:
        raise CompressError(f"输出文件无法完整解码: {' / '.join(lines) or '解码失败'}")


def _top_level_boxes(data: bytes):
    offset = 0
    total = len(data)
    while offset + 8 <= total:
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        box_type = data[offset + 4 : offset + 8]
        header = 8
        if size == 1:
            if offset + 16 > total:
                return
            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            header = 16
        elif size == 0:
            size = total - offset
        if size < header or offset + size > total:
            return
        yield offset, size, header, box_type
        offset += size


def extract_vivo_uuid_box(path: str) -> Optional[bytes]:
    """Return the exact top-level vivoMediaExtInfo uuid box, or None."""
    with open(path, "rb") as handle:
        data = handle.read()
    for offset, size, header, box_type in _top_level_boxes(data):
        if box_type != b"uuid":
            continue
        user_type = data[offset + header : offset + header + 16]
        if user_type == VIVO_UUID_USER_TYPE:
            return data[offset : offset + size]
    return None


def append_vivo_uuid_box(source_mp4: str, encoded_mp4: str) -> bool:
    """Append the original vivo uuid box to the encoded output."""
    box = extract_vivo_uuid_box(source_mp4)
    if box is None:
        return False
    existing = extract_vivo_uuid_box(encoded_mp4)
    if existing == box:
        return True
    if existing is not None:
        raise CompressError("输出文件已包含不同的 vivo uuid box，拒绝重复追加")
    with open(encoded_mp4, "ab") as handle:
        handle.write(box)
    return True


def is_profitable(original_size: int, compressed_size: int) -> bool:
    return compressed_size < original_size


def compress_file(
    src: str,
    dst: str,
    preset: Preset,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    timeout: int = 900,
    compute_ssim: bool = True,
    require_vivo_uuid: bool = True,
    speed: str = "slow",
) -> CompressResult:
    """Compress one MP4 into ``dst``; never modifies ``src``."""
    info = probe_video(src, ffprobe)
    reason = hdr_reason(info)
    if reason:
        raise HdrUnsupported(reason)
    if not info.has_video:
        raise UnsupportedFormat("没有视频流")
    if info.codec_name and info.pix_fmt not in SAFE_PIX_FMTS and preset.mode != "copy":
        raise UnsupportedFormat(f"暂不支持像素格式 {info.pix_fmt}")
    original_size = os.path.getsize(src)

    directory = os.path.dirname(os.path.abspath(dst)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".adbclean-", suffix=".mp4")
    os.close(fd)
    audio_reencoded = False
    try:
        audio_reencoded = encode_video(
            src, temp_path, preset, info, ffmpeg, timeout, speed=speed
        )
        if require_vivo_uuid and not append_vivo_uuid_box(src, temp_path):
            raise NoVivoUuid("原 MP4 没有 vivoMediaExtInfo uuid box，可能不是 vivo 实况照片")
        if not require_vivo_uuid:
            append_vivo_uuid_box(src, temp_path)
        verify_decodable(temp_path, ffmpeg, timeout)
        compressed_size = os.path.getsize(temp_path)
        if preset.mode != "copy" and not is_profitable(original_size, compressed_size):
            raise NoBenefit(
                f"压缩后没有变小（{original_size} → {compressed_size} 字节），已放弃"
            )
        ssim = None
        if compute_ssim:
            ssim = measure_ssim(src, temp_path, ffmpeg, timeout)
        os.replace(temp_path, dst)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

    compressed_size = os.path.getsize(dst)
    return CompressResult(
        original_size=original_size,
        compressed_size=compressed_size,
        saved_bytes=original_size - compressed_size,
        ratio=(compressed_size / original_size) if original_size else 1.0,
        ssim=ssim,
        duration=info.duration,
        uuid_preserved=extract_vivo_uuid_box(dst) is not None,
        audio_reencoded=audio_reencoded,
    )


def find_tool(name: str) -> Optional[str]:
    return shutil.which(name)
