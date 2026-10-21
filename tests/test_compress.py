import shutil
import struct
import subprocess

import pytest

from adbclean.compress import (
    PRESETS,
    CompressError,
    HdrUnsupported,
    NoBenefit,
    VideoInfo,
    append_vivo_uuid_box,
    build_encode_command,
    compress_file,
    extract_vivo_uuid_box,
    hdr_reason,
    is_profitable,
    parse_probe_json,
    resolve_preset,
)


def info(**overrides):
    base = dict(
        duration=2.0,
        size=4_000_000,
        width=1920,
        height=1080,
        display_width=1920,
        display_height=1080,
        codec_name="h264",
        pix_fmt="yuvj420p",
        color_range="pc",
        color_space="bt470bg",
        color_transfer="iec61966-2-1",
        color_primaries="smpte432",
        rotation=0,
        bit_depth=8,
        has_video=True,
        has_audio=True,
        audio_codec="aac",
        time_base="1/90000",
        format_tags={"com.android.version": "16"},
        video_tags={"handler_name": "VideoHandle"},
        audio_tags={"handler_name": "SoundHandle"},
    )
    base.update(overrides)
    return base


def video_info(**overrides):
    return VideoInfo(**info(**overrides))


def probe_payload(**overrides):
    stream = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "pix_fmt": "yuvj420p",
        "color_range": "pc",
        "color_space": "bt470bg",
        "color_transfer": "iec61966-2-1",
        "color_primaries": "smpte432",
        "time_base": "1/90000",
        "duration": "2.206125",
        "tags": {"handler_name": "VideoHandle"},
    }
    audio = {"codec_type": "audio", "codec_name": "aac", "tags": {"handler_name": "SoundHandle"}}
    stream.update(overrides.pop("stream", {}))
    payload = {
        "streams": [audio, stream],
        "format": {
            "duration": "2.206125",
            "size": "3425268",
            "tags": {"com.android.version": "16", "major_brand": "mp42"},
        },
    }
    payload.update(overrides)
    return payload


def make_uuid_box(payload=b"livephoto-id", user_type=b"vivoMediaExtInfo"):
    body = user_type + payload
    return struct.pack(">I", 8 + len(body)) + b"uuid" + body


def make_box(box_type, payload=b"", largesize=None):
    if largesize is not None:
        return struct.pack(">I", 1) + box_type + struct.pack(">Q", largesize) + payload
    return struct.pack(">I", 8 + len(payload)) + box_type + payload


def test_parse_probe_json_rotation_and_audio():
    parsed = parse_probe_json(probe_payload(stream={"side_data_list": [{"rotation": -90}]}))
    assert parsed.display_width == 1080
    assert parsed.display_height == 1920
    assert parsed.rotation == 270
    assert parsed.has_audio is True
    assert parsed.audio_codec == "aac"
    assert parsed.format_tags["com.android.version"] == "16"
    assert parsed.time_base == "1/90000"


def test_parse_probe_json_bit_depth():
    parsed = parse_probe_json(probe_payload(stream={"pix_fmt": "yuv420p10le"}))
    assert parsed.bit_depth == 10
    assert hdr_reason(parsed)
    ten_bit = parse_probe_json(probe_payload(stream={"pix_fmt": "yuvj420p"}))
    assert ten_bit.bit_depth == 8
    assert hdr_reason(ten_bit) is None


def test_hdr_reason_detects_transfer_and_primaries():
    assert hdr_reason(video_info(color_transfer="smpte2084"))
    assert hdr_reason(video_info(color_primaries="bt2020"))
    assert hdr_reason(video_info()) is None


def test_resolve_preset_and_custom_crf():
    assert resolve_preset("balanced").crf == 20
    assert resolve_preset("visually_lossless").crf == 18
    assert resolve_preset("lossless").mode == "copy"
    assert resolve_preset("custom", 26).crf == 26
    with pytest.raises(ValueError):
        resolve_preset("custom")
    with pytest.raises(ValueError):
        resolve_preset("custom", 52)
    with pytest.raises(ValueError):
        resolve_preset("nope")


def test_build_encode_command_keeps_vivo_compatible_flags():
    command = build_encode_command("in.mp4", "out.mp4", PRESETS["balanced"], video_info())
    joined = " ".join(command)
    for token in (
        "-map 0",
        "-c:v libx264",
        "-preset slow",
        "-crf 20",
        "-pix_fmt yuvj420p",
        "-profile:v high",
        "-level 4.0",
        "-color_range pc",
        "-colorspace bt470bg",
        "-color_primaries smpte432",
        "-color_trc iec61966-2-1",
        "-c:a copy",
        "-vsync 0",
        "-enc_time_base 1/90000",
        "-brand mp42",
        "+faststart+use_metadata_tags",
        "com.android.version=16",
        "handler_name=VideoHandle",
        "handler_name=SoundHandle",
    ):
        assert token in joined
    assert "-dn" in command and "-sn" in command


def test_build_encode_command_omits_level_for_4k():
    command = build_encode_command(
        "in.mp4", "out.mp4", PRESETS["balanced"], video_info(width=3840, height=2160)
    )
    assert "-level" not in command


def test_build_encode_command_for_rotated_portrait():
    command = build_encode_command(
        "in.mp4",
        "out.mp4",
        PRESETS["small"],
        video_info(rotation=270, display_width=1080, display_height=1920),
    )
    assert "-level" in command
    assert command[command.index("-level") + 1] == "4.0"


def test_build_encode_command_copy_mode():
    command = build_encode_command("in.mp4", "out.mp4", PRESETS["lossless"], video_info())
    assert "-c" in command and "copy" in command
    assert "-crf" not in command
    assert "-vsync" not in command


def test_build_encode_command_ignores_unknown_color_values():
    command = build_encode_command(
        "in.mp4",
        "out.mp4",
        PRESETS["balanced"],
        video_info(color_space="evil; rm -rf", color_primaries="nope", color_transfer="?"),
    )
    assert "-colorspace" not in command
    assert "-color_primaries" not in command
    assert "-color_trc" not in command


def test_build_encode_command_no_audio():
    command = build_encode_command(
        "in.mp4", "out.mp4", PRESETS["balanced"], video_info(has_audio=False)
    )
    assert "-c:a" not in command


def test_extract_vivo_uuid_box_exact_bytes(tmp_path):
    box = make_uuid_box()
    path = tmp_path / "sample.mp4"
    path.write_bytes(make_box(b"ftyp", b"mp42") + make_box(b"mdat", b"data") + box)
    assert extract_vivo_uuid_box(str(path)) == box


def test_extract_vivo_uuid_box_64bit_size(tmp_path):
    box = make_uuid_box()
    largesize = len(box) + 8
    header = struct.pack(">I", 1) + b"uuid" + struct.pack(">Q", largesize)
    path = tmp_path / "sample.mp4"
    path.write_bytes(make_box(b"ftyp", b"mp42") + header + box[8:])
    assert extract_vivo_uuid_box(str(path)) == header + box[8:]


def test_extract_vivo_uuid_box_missing_and_truncated(tmp_path):
    path = tmp_path / "plain.mp4"
    path.write_bytes(make_box(b"ftyp", b"mp42") + make_box(b"mdat", b"data"))
    assert extract_vivo_uuid_box(str(path)) is None

    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(struct.pack(">I", 999) + b"uuid" + b"short")
    assert extract_vivo_uuid_box(str(truncated)) is None


def test_append_vivo_uuid_box_is_idempotent(tmp_path):
    box = make_uuid_box()
    source = tmp_path / "src.mp4"
    source.write_bytes(make_box(b"ftyp", b"mp42") + box)
    target = tmp_path / "out.mp4"
    target.write_bytes(make_box(b"ftyp", b"mp42") + make_box(b"mdat", b"encoded"))
    assert append_vivo_uuid_box(str(source), str(target)) is True
    first = target.read_bytes()
    assert first.endswith(box)
    assert append_vivo_uuid_box(str(source), str(target)) is True
    assert target.read_bytes() == first


def test_append_missing_uuid_returns_false(tmp_path):
    source = tmp_path / "src.mp4"
    source.write_bytes(make_box(b"ftyp", b"mp42"))
    target = tmp_path / "out.mp4"
    target.write_bytes(make_box(b"ftyp", b"mp42"))
    assert append_vivo_uuid_box(str(source), str(target)) is False


def test_is_profitable():
    assert is_profitable(100, 99)
    assert not is_profitable(100, 100)
    assert not is_profitable(100, 101)


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")
def test_compress_file_end_to_end_with_synthetic_uuid(tmp_path):
    source = tmp_path / "src.mp4"
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=0.6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.6",
            "-c:v", "libx264", "-b:v", "4M", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ],
        check=True,
    )
    with open(source, "ab") as handle:
        handle.write(make_uuid_box())
    target = tmp_path / "out.mp4"
    result = compress_file(
        str(source), str(target), PRESETS["small"], ffmpeg=FFMPEG, ffprobe=FFPROBE
    )
    assert target.exists()
    assert result.uuid_preserved is True
    assert result.compressed_size < result.original_size
    assert result.ssim is None or 0.0 < result.ssim <= 1.0
    assert extract_vivo_uuid_box(str(target)) == make_uuid_box()


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")
def test_compress_file_rejects_missing_uuid(tmp_path):
    source = tmp_path / "src.mp4"
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30:duration=0.4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
    )
    with pytest.raises(CompressError):
        compress_file(
            str(source),
            str(tmp_path / "out.mp4"),
            PRESETS["balanced"],
            ffmpeg=FFMPEG,
            ffprobe=FFPROBE,
        )


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")
def test_compress_copy_mode_without_benefit_is_allowed(tmp_path):
    source = tmp_path / "src.mp4"
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30:duration=0.4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
    )
    with open(source, "ab") as handle:
        handle.write(make_uuid_box())
    result = compress_file(
        str(source),
        str(tmp_path / "out.mp4"),
        PRESETS["lossless"],
        ffmpeg=FFMPEG,
        ffprobe=FFPROBE,
        compute_ssim=False,
    )
    assert result.uuid_preserved is True


def test_compress_result_no_benefit_class_is_compress_error():
    assert issubclass(NoBenefit, CompressError)
    assert issubclass(HdrUnsupported, CompressError)
