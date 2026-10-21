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


class HardwareEncoderError(CompressError):
    """VideoToolbox refused to open an encoder session."""


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
MIN_HARDWARE_SAVINGS = 0.10

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
    frame_rate: float
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
    speed_used: str = "slow"
    hardware_quality_used: Optional[int] = None
    crf_used: Optional[int] = None
    fallback_note: str = ""


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


def _parse_rate(value) -> float:
    text = str(value or "")
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        try:
            num = float(numerator)
            den = float(denominator)
        except ValueError:
            return 0.0
        return num / den if den else 0.0
    return _as_float(text)


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
        frame_rate=_parse_rate(video.get("avg_frame_rate"))
        or _parse_rate(video.get("r_frame_rate")),
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
    """Return level 4.0 only when the stream actually fits level 4.0.

    H.264 level is constrained by macroblocks per frame and per second, not
    just the longest edge. 1728x1296@30, for example, exceeds level 4.0.
    """
    width_mbs = (info.width + 15) // 16
    height_mbs = (info.height + 15) // 16
    frame_mbs = width_mbs * height_mbs
    if info.frame_rate <= 0:
        return None
    if frame_mbs <= 8192 and frame_mbs * info.frame_rate <= 245760:
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
    hardware_q: Optional[int] = None,
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
        # H.264 4:2:0 requires even dimensions. Cropped videos can be odd;
        # trim at most one pixel instead of resizing or changing the aspect.
        if info.width % 2 or info.height % 2:
            command.extend(["-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2"])
        if speed == "hardware":
            quality = hardware_q if hardware_q is not None else hardware_quality(preset.crf)
            command.extend(["-c:v", "h264_videotoolbox", "-q:v", str(quality)])
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
    hardware_q: Optional[int] = None,
) -> bool:
    """Encode src to dst. Returns True when audio had to be re-encoded."""
    command = build_encode_command(src, dst, preset, info, ffmpeg, speed, hardware_q)
    result = _run(command, timeout)
    if result.returncode == 0:
        return False
    stderr = result.stderr.decode("utf-8", "replace").strip()
    if speed == "hardware" and _looks_like_hardware_failure(stderr):
        raise HardwareEncoderError(stderr or "VideoToolbox 无法初始化编码器")
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


def _looks_like_hardware_failure(stderr: str) -> bool:
    text = stderr.lower()
    markers = (
        "h264_videotoolbox",
        "cannot prepare encoder",
        "error while opening encoder",
        "-12902",
    )
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
    """Compress one MP4 into ``dst``; never modifies ``src``.

    Hardware encoding is fast but at a fixed high quality it can save very
    little on already-efficient sources. If savings are below 10%, retry
    with software x264 at the same CRF, then (for the balanced preset) at
    CRF23. The smallest profitable result wins; otherwise the file is
    skipped. VideoToolbox init failures fall back to software directly.
    """
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
    best_path = temp_path + ".best.mp4"

    best_size: Optional[int] = None
    best_speed = speed
    best_q: Optional[int] = None
    best_crf: Optional[int] = None
    best_audio = False
    best_label = ""
    hardware_failed = False
    fallback_notes: List[str] = []

    def run_attempt(attempt_speed: str, attempt_q: Optional[int], attempt_preset: Preset):
        audio = encode_video(
            src,
            temp_path,
            attempt_preset,
            info,
            ffmpeg,
            timeout,
            speed=attempt_speed,
            hardware_q=attempt_q,
        )
        if require_vivo_uuid and not append_vivo_uuid_box(src, temp_path):
            raise NoVivoUuid(
                "原 MP4 没有 vivoMediaExtInfo uuid box，可能不是 vivo 实况照片"
            )
        if not require_vivo_uuid:
            append_vivo_uuid_box(src, temp_path)
        verify_decodable(temp_path, ffmpeg, timeout)
        return os.path.getsize(temp_path), audio

    def record(
        size: int,
        audio: bool,
        attempt_speed: str,
        attempt_q: Optional[int],
        label: str,
        crf: Optional[int],
    ):
        nonlocal best_size, best_speed, best_q, best_crf, best_audio, best_label
        if preset.mode != "copy" and not is_profitable(original_size, size):
            return False
        if best_size is None or size < best_size:
            os.replace(temp_path, best_path)
            best_size = size
            best_speed = attempt_speed
            best_q = attempt_q
            best_crf = crf
            best_audio = audio
            best_label = label
        return True

    def savings_pct(size: int) -> float:
        return (original_size - size) / original_size if original_size else 0.0

    hardware_ok = False
    hardware_size = 0
    try:
        if preset.mode == "copy":
            size, audio = run_attempt(speed, None, preset)
            record(size, audio, speed, None, "无损重封装", None)
        elif speed != "hardware":
            size, audio = run_attempt(speed, None, preset)
            record(size, audio, speed, None, f"软件 {speed}", preset.crf)
        else:
            hardware_ok = False
            hardware_size = 0
            try:
                hardware_size, hardware_audio = run_attempt("hardware", None, preset)
                hardware_ok = True
                record(
                    hardware_size,
                    hardware_audio,
                    "hardware",
                    hardware_quality(preset.crf),
                    "硬件加速",
                    preset.crf,
                )
            except HardwareEncoderError:
                hardware_failed = True

            hardware_savings = savings_pct(hardware_size) if hardware_ok else 0.0
            need_software = (
                hardware_failed
                or not hardware_ok
                or hardware_size >= original_size
                or hardware_savings < MIN_HARDWARE_SAVINGS
            )
            if need_software:
                software_size, software_audio = run_attempt("veryfast", None, preset)
                software_savings = savings_pct(software_size)
                record(
                    software_size,
                    software_audio,
                    "veryfast",
                    None,
                    f"软件 CRF{preset.crf}",
                    preset.crf,
                )
                if preset.key == "balanced" and software_savings < MIN_HARDWARE_SAVINGS:
                    small_preset = PRESETS["small"]
                    small_size, small_audio = run_attempt("veryfast", None, small_preset)
                    record(
                        small_size,
                        small_audio,
                        "veryfast",
                        None,
                        "软件 CRF23",
                        small_preset.crf,
                    )

        if best_size is None:
            raise NoBenefit(
                f"压缩后没有变小（{original_size} 字节），已尝试硬件和软件编码"
            )

        hardware_profitable = hardware_ok and hardware_size < original_size
        if hardware_failed:
            fallback_notes.append("硬件编码初始化失败，已回退软件")
        elif speed == "hardware" and best_label and best_label != "硬件加速":
            prefix = (
                f"硬件节省不足 {MIN_HARDWARE_SAVINGS:.0%}"
                if hardware_profitable
                else "硬件编码无收益"
            )
            fallback_notes.append(f"{prefix}，已改用{best_label}")
        elif speed == "hardware" and best_label == "硬件加速":
            saved = savings_pct(best_size)
            if saved < MIN_HARDWARE_SAVINGS:
                fallback_notes.append(f"源文件已较紧凑，实际节省 {saved:.1%}")
        if (info.width % 2 or info.height % 2) and preset.mode != "copy":
            fallback_notes.append("奇数分辨率已裁掉 1 像素")

        ssim = measure_ssim(src, best_path, ffmpeg, timeout) if compute_ssim else None
        os.replace(best_path, dst)
    except Exception:
        for path in (temp_path, best_path):
            if os.path.exists(path):
                os.unlink(path)
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
        audio_reencoded=best_audio,
        speed_used=best_speed,
        hardware_quality_used=best_q,
        crf_used=best_crf,
        fallback_note="；".join(fallback_notes),
    )

def find_tool(name: str) -> Optional[str]:
    return shutil.which(name)
