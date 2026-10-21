import hashlib
import http.client
import json
import threading
import urllib.parse

import pytest

from adbclean.adb import ADB
from adbclean.compress import PRESETS, CompressError
from adbclean.live import LivePair
from adbclean.scan import Scan
from adbclean.studio import (
    BadRequest,
    ItemResult,
    Job,
    StudioHandler,
    StudioServer,
    StudioState,
    _album_of,
    _backup_file,
    _backup_sessions,
    _backup_verified,
    _compress_job,
    _delete_backup_session,
    _history_entry,
    _history_skip_reason,
    _job_file,
    _job_progress,
    _load_history,
    _migrate_legacy_backups,
    _photo_payload,
    _read_backup_session,
    _save_history,
    _string_list,
    _write_restore_script,
    estimate_local_need,
)


def make_pair(mp4, size=1_000_000, date="2026-01-02", time="03:04:05", duration=2000):
    jpg = mp4[:-4] + ".jpg"
    return LivePair(mp4=mp4, jpg=jpg, size=size, duration_ms=duration, date=date, time=time)


def make_pairs():
    return [
        make_pair("/sdcard/DCIM/Camera/IMG_20260102_030405.mp4", 100, "2026-01-02", "03:04:05"),
        make_pair("/sdcard/DCIM/Camera/IMG_20260102_030406.mp4", 200, "2026-01-02", "03:04:06"),
        make_pair("/sdcard/Pictures/ChatApp/IMG_20260104_050607.mp4", 300, "2026-01-04"),
        make_pair("/sdcard/DCIM/Camera/IMG_20260105_060708.mp4", 400, "2026-01-05"),
    ]


def make_state(pairs=None):
    pairs = pairs if pairs is not None else make_pairs()
    scan = Scan(serial="SERIAL", root="/sdcard", scanned_at="2026-01-03T13:38:53+08:00")
    return StudioState(
        adb=ADB(adb_path="adb", serial="SERIAL"),
        scan=scan,
        pairs=pairs,
        pairs_by_mp4={pair.mp4: pair for pair in pairs},
        durations={},
        token="test-token",
        port=0,
        ffmpeg="nonexistent-ffmpeg",
        ffprobe="nonexistent-ffprobe",
        max_duration=5000,
        model="TESTDEVICE",
        android="16",
    )


def test_album_of():
    assert _album_of("/sdcard/DCIM/Camera/IMG_1.mp4") == "/sdcard/DCIM/Camera"
    assert _album_of("IMG_1.mp4") == "/"


def test_state_grouping_dates_months_albums():
    state = make_state()
    dates = state.dates()
    assert [row["date"] for row in dates] == ["2026-01-05", "2026-01-04", "2026-01-02"]
    assert dates[0]["count"] == 1
    assert dates[0]["bytes"] == 400
    months = state.months()
    assert months[0] == {"month": "2026-01", "count": 4, "bytes": 1000}
    albums = state.albums()
    assert albums[0]["album"] == "/sdcard/DCIM/Camera"
    assert albums[0]["name"] == "Camera"
    assert albums[0]["count"] == 3
    assert albums[0]["bytes"] == 700
    assert albums[1]["album"] == "/sdcard/Pictures/ChatApp"


def test_pairs_for_filters():
    state = make_state()
    assert len(state.pairs_for("2026-01-02")) == 2
    assert len(state.pairs_for_month("2026-01")) == 4
    assert len(state.pairs_for_album("/sdcard/DCIM/Camera")) == 3
    assert state.pairs_for_album("/sdcard/Nope") == []


def test_photo_payload_contains_album():
    payload = _photo_payload(make_pairs()[0])
    assert payload["album"] == "/sdcard/DCIM/Camera"
    assert payload["unknown_duration"] is False


def test_history_entry_and_payload():
    pair = make_pair("/sdcard/DCIM/Camera/IMG_20260102_030405.mp4", size=50)
    state = make_state([pair])
    state.history[pair.mp4] = {"compressed_size": 50, "crf_used": 23}
    assert _history_entry(state, pair)["crf_used"] == 23
    payload = _photo_payload(pair, state)
    assert payload["compressed"] is True
    assert payload["history_crf"] == 23
    pair.size = 51
    assert _history_entry(state, pair) is None
    assert _photo_payload(pair, state)["compressed"] is False


def test_history_skip_reason():
    entry = {"compressed_size": 50, "crf_used": 20}
    assert _history_skip_reason(None, False, PRESETS["balanced"]) == ""
    assert "已压缩过" in _history_skip_reason(entry, False, PRESETS["balanced"])
    assert "不是更深压缩" in _history_skip_reason(
        entry, True, PRESETS["visually_lossless"]
    )
    assert _history_skip_reason(entry, True, PRESETS["small"]) == ""
    assert "无损重封装" in _history_skip_reason(entry, True, PRESETS["lossless"])


def test_history_roundtrip(tmp_path, monkeypatch):
    import adbclean.studio as studio

    monkeypatch.setattr(studio, "HISTORY_PATH", tmp_path / "history.json")
    _save_history({"/sdcard/a.mp4": {"compressed_size": 1}})
    assert _load_history() == {"/sdcard/a.mp4": {"compressed_size": 1}}


def test_string_list_dedupes_and_validates():
    assert _string_list({"paths": ["b", "a", "b"]}) == ["b", "a"]
    with pytest.raises(BadRequest):
        _string_list({"paths": "a"})
    with pytest.raises(BadRequest):
        _string_list({"paths": [1, 2]})
    large = ["p{}".format(i) for i in range(1200)]
    assert len(_string_list({"paths": large})) == 1200


def test_backup_file_rejects_traversal(tmp_path):
    assert _backup_file("") is None
    assert _backup_file("../escape.mp4") is None
    assert _backup_file("/etc/passwd") is None
    assert _backup_file("20260103-040506/sdcard/DCIM/Camera/x.mp4") is not None


def test_job_file_rejects_traversal():
    state = make_state()
    job = Job(id="abc123", kind="compress", total=0)
    state.jobs[job.id] = job
    assert _job_file(state, "missing", "src/x.mp4") is None
    assert _job_file(state, "abc123", "") is None
    assert _job_file(state, "abc123", "../../etc/passwd") is None
    resolved = _job_file(state, "abc123", "src/x.mp4")
    assert resolved is not None
    assert "abc123" in str(resolved)


def test_write_restore_script(tmp_path):
    path = _write_restore_script(
        tmp_path,
        [("sdcard/DCIM/Camera/IMG_1.mp4", "/sdcard/DCIM/Camera/IMG_1.mp4", 1792498634)],
    )
    content = path.read_text(encoding="utf-8")
    assert '"$ADB" push "$HERE"/' in content
    assert "chmod 660" in content
    assert "touch -d @1792498634" in content
    assert "mv -f" in content
    assert "MEDIA_SCANNER_SCAN_FILE" in content


class APIClient:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, token=None, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        request_headers = dict(headers or {})
        if token:
            request_headers["X-Adbclean-Token"] = token
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=payload, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, data
        finally:
            connection.close()

    def json(self, method, path, token=None, body=None, headers=None):
        status, data = self.request(method, path, token, body, headers)
        return status, json.loads(data.decode("utf-8") or "{}")


@pytest.fixture()
def studio_server(tmp_path, monkeypatch):
    import adbclean.studio as studio

    monkeypatch.setattr(studio, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(studio, "JOBS_DIR", tmp_path / "jobs")
    state = make_state()
    server = StudioServer(("127.0.0.1", 0), StudioHandler, state)
    state.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield APIClient(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_requires_token(studio_server):
    status, _ = studio_server.request("GET", "/api/summary")
    assert status == 403
    status, data = studio_server.json("GET", "/api/summary", token="test-token")
    assert status == 200
    assert data["totals"]["pairs"] == 4
    assert data["device"]["serial"] != "SERIAL"
    assert data["ffmpeg"]["ok"] is False


def test_api_dates_and_filters(studio_server):
    status, data = studio_server.json("GET", "/api/dates?token=test-token")
    assert status == 200
    assert data["dates"][0]["date"] == "2026-01-05"
    assert data["months"][0]["month"] == "2026-01"
    assert data["albums"][0]["name"] == "Camera"

    status, data = studio_server.json(
        "GET", "/api/photos?date=2026-01-02&token=test-token"
    )
    assert status == 200
    assert len(data["photos"]) == 2
    assert all(photo["album"] == "/sdcard/DCIM/Camera" for photo in data["photos"])

    album = urllib.parse.quote("/sdcard/Pictures/ChatApp", safe="")
    status, data = studio_server.json("GET", "/api/photos?album=" + album + "&token=test-token")
    assert status == 200
    assert len(data["photos"]) == 1

    status, _ = studio_server.json(
        "GET", "/api/photos?album=/sdcard/evil&token=test-token"
    )
    assert status == 400

    status, _ = studio_server.json(
        "GET", "/api/photos?date=../../etc&token=test-token"
    )
    assert status == 400


def test_api_largest_and_bad_thumb(studio_server):
    status, data = studio_server.json("GET", "/api/largest?limit=2&token=test-token")
    assert status == 200
    assert [photo["size"] for photo in data["photos"]] == [400, 300]

    status, _ = studio_server.json(
        "GET", "/api/thumb?path=/sdcard/nope.mp4&token=test-token"
    )
    assert status == 400


def test_api_post_security(studio_server):
    status, _ = studio_server.json(
        "POST", "/api/compress", body={"paths": [], "preset": "balanced"}
    )
    assert status == 403

    status, _ = studio_server.json(
        "POST",
        "/api/compress",
        token="test-token",
        body={"paths": [], "preset": "balanced"},
        headers={"Origin": "http://evil.example"},
    )
    assert status == 403

    status, _ = studio_server.request(
        "GET", "/api/summary", token="test-token", headers={"Host": "evil.example"}
    )
    assert status == 403

    status, _ = studio_server.json(
        "POST", "/api/compress", token="test-token", body={"paths": [], "preset": "balanced"}
    )
    assert status == 400


def test_api_delete_backups_requires_confirmation(studio_server):
    status, _ = studio_server.json(
        "POST", "/api/delete-backups", token="test-token", body={"session": "x"}
    )
    assert status == 400
    status, _ = studio_server.json(
        "POST",
        "/api/delete-backups",
        token="test-token",
        body={"session": "x", "confirm": "delete-backups"},
    )
    assert status == 400


def test_job_progress_tracks_partial_items():
    job = Job(id="progress1", kind="compress", total=3)
    job.items = [
        ItemResult(
            "/sdcard/a.mp4", "/sdcard/a.jpg", "2026-01-02", "03:04:05",
            status="ready",
        ),
        ItemResult(
            "/sdcard/b.mp4", "/sdcard/b.jpg", "2026-01-02", "03:04:06",
            status="running", progress=0.5,
        ),
        ItemResult(
            "/sdcard/c.mp4", "/sdcard/c.jpg", "2026-01-02", "03:04:07",
            status="pending",
        ),
    ]
    assert _job_progress(job) == pytest.approx(0.5)


def test_job_progress_empty_done_is_complete():
    assert _job_progress(Job(id="empty1", kind="scan", total=1, status="done")) == 1.0


def test_api_media_rejects_unknown_path(studio_server):
    status, _ = studio_server.json(
        "GET", "/api/media?path=/sdcard/nope.mp4&token=test-token"
    )
    assert status == 400


def test_api_unknown_job_and_preview(studio_server):
    status, _ = studio_server.json("GET", "/api/jobs/doesnotexist?token=test-token")
    assert status == 404
    status, _ = studio_server.json(
        "GET", "/api/preview?job=x&path=/sdcard/a.mp4&token=test-token"
    )
    assert status == 400


def write_manifest(session_dir, rows):
    session_dir.mkdir(parents=True, exist_ok=True)
    manifest = session_dir / "manifest.tsv"
    lines = ["mp4\tjpg\toriginal_size\tnew_size\toriginal_md5\tnew_md5\tmtime"]
    lines.extend("\t".join(str(cell) for cell in row) for row in rows)
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_read_backup_session(tmp_path):
    session = tmp_path / "20260103-040506"
    old_md5 = "a" * 32
    new_md5 = "b" * 32
    write_manifest(
        session,
        [
            [
                "/sdcard/DCIM/Camera/IMG_20260103_040506.mp4",
                "/sdcard/DCIM/Camera/IMG_20260103_040506.jpg",
                8_497_773,
                3_317_325,
                old_md5,
                new_md5,
                1792551907,
            ],
            ["/sdcard/broken.mp4", "/sdcard/broken.jpg", "not-a-size", 1, old_md5, new_md5, 1],
        ],
    )
    info = _read_backup_session(session)
    assert info is not None
    assert info["count"] == 1
    assert info["bytes"] == 8_497_773
    entry = info["entries"][0]
    assert entry["date"] == "2026-01-03"
    assert entry["time"] == "04:05:06"
    assert entry["backup"] == (
        "20260103-040506/sdcard/DCIM/Camera/IMG_20260103_040506.mp4"
    )
    assert _backup_file(entry["backup"]) is not None


def test_read_backup_session_missing_or_empty(tmp_path):
    assert _read_backup_session(tmp_path / "nope") is None
    empty = tmp_path / "20260103-040507"
    empty.mkdir()
    assert _read_backup_session(empty) is None


def test_backup_sessions_sorted_desc(tmp_path, monkeypatch):
    import adbclean.studio as studio

    monkeypatch.setattr(studio, "BACKUPS_DIR", tmp_path / "backups")
    old_md5 = "c" * 32
    new_md5 = "d" * 32
    for name in ("20260103-030405", "20260103-040506"):
        write_manifest(
            tmp_path / "backups" / name,
            [
                [
                    "/sdcard/DCIM/Camera/IMG_20260103_040506.mp4",
                    "/sdcard/DCIM/Camera/IMG_20260103_040506.jpg",
                    100,
                    50,
                    old_md5,
                    new_md5,
                    1,
                ]
            ],
        )
    sessions = _backup_sessions()
    assert [row["session"] for row in sessions] == ["20260103-040506", "20260103-030405"]


def test_delete_backup_session(tmp_path, monkeypatch):
    import adbclean.studio as studio

    monkeypatch.setattr(studio, "BACKUPS_DIR", tmp_path / "backups")
    session = tmp_path / "backups" / "20260103-040506"
    write_manifest(
        session,
        [
            [
                "/sdcard/DCIM/Camera/IMG_20260103_040506.mp4",
                "/sdcard/DCIM/Camera/IMG_20260103_040506.jpg",
                100,
                50,
                "a" * 32,
                "b" * 32,
                1,
            ]
        ],
    )
    assert _delete_backup_session("20260103-040506") == 1
    assert not session.exists()
    with pytest.raises(BadRequest):
        _delete_backup_session("20260103-040506")


def test_delete_backup_rejects_unknown(tmp_path, monkeypatch):
    import adbclean.studio as studio

    monkeypatch.setattr(studio, "BACKUPS_DIR", tmp_path / "backups")
    with pytest.raises(BadRequest):
        _delete_backup_session("../etc")


def test_migrate_legacy_backups(tmp_path, monkeypatch):
    import adbclean.studio as studio

    legacy = tmp_path / "cache" / "studio" / "backups"
    new = tmp_path / "data" / "studio" / "backups"
    write_manifest(
        legacy / "20260103-040506",
        [
            [
                "/sdcard/DCIM/Camera/IMG_20260103_040506.mp4",
                "/sdcard/DCIM/Camera/IMG_20260103_040506.jpg",
                100,
                50,
                "e" * 32,
                "f" * 32,
                1,
            ]
        ],
    )
    monkeypatch.setattr(studio, "LEGACY_BACKUPS_DIR", legacy)
    monkeypatch.setattr(studio, "BACKUPS_DIR", new)
    _migrate_legacy_backups()
    assert (new / "20260103-040506" / "manifest.tsv").exists()
    assert not legacy.exists()


def test_backup_verified_prefers_hardlink(tmp_path):
    source = tmp_path / "source.mp4"
    data = b"live-photo" * 5000
    source.write_bytes(data)
    target = tmp_path / "backup.mp4"
    expected = hashlib.md5(data).hexdigest()
    linked = _backup_verified(source, target, expected)
    assert target.read_bytes() == data
    if linked:
        assert source.stat().st_ino == target.stat().st_ino
    source.unlink()
    assert target.read_bytes() == data


def test_backup_verified_rejects_wrong_md5(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"data")
    target = tmp_path / "backup.mp4"
    with pytest.raises(CompressError):
        _backup_verified(source, target, "0" * 32)
    assert not target.exists()


def test_estimate_local_need():
    assert estimate_local_need(0) == 256 * 1024 * 1024
    assert estimate_local_need(1000) == 1500 + 256 * 1024 * 1024


def test_item_result_defaults():
    item = ItemResult("/sdcard/a.mp4", "/sdcard/a.jpg", "2026-01-02", "03:04:05")
    assert item.status == "pending"
    assert item.original_md5 == ""


def test_compress_job_skips_preskipped_items(tmp_path, monkeypatch):
    import adbclean.studio as studio

    monkeypatch.setattr(studio, "JOBS_DIR", tmp_path / "jobs")
    state = make_state()
    job = Job(id="skip1", kind="compress", total=1)
    item = ItemResult("/sdcard/a.mp4", "/sdcard/a.jpg", "2026-01-02", "03:04:05")
    item.status = "skipped"
    item.reason = "已压缩过"
    job.items = [item]
    _compress_job(state, job, PRESETS["balanced"], "hardware", False)
    assert job.done == 1
    assert item.status == "skipped"


def test_compress_job_cancelled_skips_items(tmp_path, monkeypatch):
    import adbclean.studio as studio

    monkeypatch.setattr(studio, "JOBS_DIR", tmp_path / "jobs")
    state = make_state()
    job = Job(id="cancel1", kind="compress", total=1, cancel_requested=True)
    job.items = [ItemResult("/sdcard/a.mp4", "/sdcard/a.jpg", "2026-01-02", "03:04:05")]
    _compress_job(state, job, PRESETS["balanced"], "veryfast", False)
    assert job.done == 1
    assert job.items[0].status == "skipped"
    assert "停止" in job.items[0].reason


def test_compress_job_marks_unknown_pair_as_error(tmp_path, monkeypatch):
    import adbclean.studio as studio

    monkeypatch.setattr(studio, "JOBS_DIR", tmp_path / "jobs")
    state = make_state()
    job = Job(id="compress1", kind="compress", total=1)
    job.items = [
        ItemResult("/sdcard/unknown.mp4", "/sdcard/unknown.jpg", "2026-01-02", "03:04:05")
    ]
    _compress_job(state, job, PRESETS["balanced"])
    assert job.done == 1
    assert job.items[0].status == "error"
    assert "配对清单" in job.items[0].reason
