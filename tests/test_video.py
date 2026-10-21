from klean.compress import HdrUnsupported, VideoInfo
from klean.video import (
    VIDEO_PRESETS,
    VideoItem,
    _build_command,
    load_videos,
    parse_media_rows,
)


class FakeADB:
    def __init__(self, text):
        self.text = text

    def content_query(self, uri, projection, timeout=180):
        return self.text


def media_row(**fields):
    values = []
    for key, value in fields.items():
        values.append(f"{key}={value}")
    return "Row: 0 " + ", ".join(values)


def video_info(**overrides):
    base = dict(
        duration=10.0,
        size=1000,
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
        frame_rate=30.0,
        has_video=True,
        has_audio=True,
        audio_codec="aac",
        time_base="1/90000",
    )
    base.update(overrides)
    return VideoInfo(**base)


def test_parse_media_rows_keeps_paths_with_spaces():
    text = media_row(
        _id=42,
        _data="/storage/emulated/0/DCIM/Camera/my video.mp4",
        duration=10000,
        width=1920,
        height=1080,
        _size=123,
        date_added=1790000000,
        mime_type="video/mp4",
    )
    rows = parse_media_rows(text)
    assert rows[0]["_id"] == "42"
    assert rows[0]["_data"].endswith("my video.mp4")
    assert rows[0]["duration"] == "10000"


def test_load_videos_excludes_live_and_short_orphans():
    rows = "\n".join(
        [
            media_row(
                _id=1,
                _data="/storage/emulated/0/DCIM/Camera/IMG_20260102_030405.mp4",
                duration=2000,
                width=1920,
                height=1080,
                _size=500,
                date_added=1790000000,
                mime_type="video/mp4",
            ),
            media_row(
                _id=2,
                _data="/storage/emulated/0/DCIM/Camera/video_20261007_205146.mp4",
                duration=10816,
                width=1920,
                height=1080,
                _size=9000,
                date_added=0,
                mime_type="video/mp4",
            ),
            media_row(
                _id=3,
                _data="/storage/emulated/0/DCIM/Camera/IMG_20260103_040506.mp4",
                duration=2000,
                width=1080,
                height=1920,
                _size=400,
                date_added=1790000000,
                mime_type="video/mp4",
            ),
            media_row(
                _id=4,
                _data="/storage/emulated/0/Android/data/app/cache/x.mp4",
                duration=9000,
                width=640,
                height=360,
                _size=300,
                date_added=1790000000,
                mime_type="video/mp4",
            ),
        ]
    )
    videos = load_videos(
        FakeADB(rows),
        live_paths={"/sdcard/DCIM/Camera/IMG_20260102_030405.mp4"},
        size_map={},
    )
    assert [video.path for video in videos] == [
        "/sdcard/DCIM/Camera/video_20261007_205146.mp4"
    ]
    assert videos[0].date == "2026-10-07"
    assert videos[0].time == "20:51:46"
    assert videos[0].size == 9000


def test_video_item_groups_and_album():
    video = VideoItem("7", "/sdcard/DCIM/Camera/a.mp4", 1, 1000, 1920, 1080, "2026-10-07", "20:51:46")
    assert video.month == "2026-10"
    assert video.album == "/sdcard/DCIM/Camera"


def test_build_video_command_uses_hardware_and_no_vivo_uuid(monkeypatch):
    monkeypatch.setattr(
        "klean.video._encoder_list",
        lambda ffmpeg: "h264_videotoolbox hevc_videotoolbox libx264 libx265",
    )
    command, encoder, quality, hardware = _build_command(
        "in.mp4", "out.mp4", VIDEO_PRESETS["balanced"], video_info(), "ffmpeg"
    )
    assert encoder == "h264_videotoolbox"
    assert quality == 60
    assert hardware is True
    assert "-progress" not in command
    assert "-q:v" in command
    assert all("vivo" not in part.lower() for part in command)


def test_build_video_command_rejects_hdr(monkeypatch):
    monkeypatch.setattr("klean.video._encoder_list", lambda ffmpeg: "h264_videotoolbox")
    try:
        _build_command(
            "in.mp4", "out.mp4", VIDEO_PRESETS["balanced"], video_info(bit_depth=10), "ffmpeg"
        )
    except HdrUnsupported:
        pass
    else:
        raise AssertionError("10-bit video should be rejected")
