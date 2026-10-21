"""Local web workbench for compressing vivo live-photo MP4 companions.

The server binds to loopback only, requires a random per-session token, and
refuses any path that is not an exact member of the verified live-photo pair
list. JPGs are never written. Replacing an MP4 always backs up the original
first and verifies MD5 before and after the atomic rename.
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .adb import ADB, ADBError
from .compress import (
    PRESET_ORDER,
    PRESETS,
    SPEED_LABELS,
    SPEED_ORDER,
    UI_CRF_MAX,
    UI_CRF_MIN,
    CompressError,
    HdrUnsupported,
    NoBenefit,
    NoVivoUuid,
    UnsupportedFormat,
    compress_file,
    detect_hardware_encoder,
    resolve_preset,
    resolve_speed,
)
from .live import LivePair, find_pairs, load_durations, sh_quote
from .scan import Scan, load_latest, save_scan, scan_device
from .util import data_dir, human, mask_serial, now_iso, read_json, stamp, state_dir, write_json
from .video import (
    VIDEO_BATCH_SUGGESTION,
    VIDEO_PRESETS,
    VIDEO_TIMEOUT,
    VIDEO_WORKERS,
    VideoItem,
    albums_for,
    compress_regular_video,
    dates_for,
    load_videos,
    months_for,
)

WEB_DIR = Path(__file__).resolve().with_name("web")
STUDIO_DIR = state_dir() / "studio"
JOBS_DIR = STUDIO_DIR / "jobs"
THUMBS_DIR = STUDIO_DIR / "thumbs"
VIDEO_THUMBS_DIR = STUDIO_DIR / "video-thumbs"
BACKUPS_DIR = data_dir() / "studio" / "backups"
VIDEO_BACKUPS_DIR = data_dir() / "studio" / "video-backups"
HISTORY_PATH = data_dir() / "studio" / "history.json"
VIDEO_HISTORY_PATH = data_dir() / "studio" / "video-history.json"
LEGACY_BACKUPS_DIR = STUDIO_DIR / "backups"
MAX_BODY = 1024 * 1024
BATCH_SUGGESTION = 200
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
THUMB_WIDTH = 480


class BadRequest(Exception):
    """Invalid client input; mapped to HTTP 400."""


class Busy(Exception):
    """Another exclusive job is running; mapped to HTTP 409."""


@dataclass
class ItemResult:
    mp4: str
    jpg: str
    date: str
    time: str
    status: str = "pending"
    original_size: int = 0
    compressed_size: int = 0
    ssim: Optional[float] = None
    reason: str = ""
    original_md5: str = ""
    compressed_md5: str = ""
    output: str = ""
    source: str = ""
    backup: str = ""
    mtime: int = 0
    duration_ms: Optional[int] = None
    elapsed_ms: int = 0
    crf_used: Optional[int] = None
    speed_used: str = ""
    progress: float = 0.0
    phase: str = ""


@dataclass
class Job:
    id: str
    kind: str
    total: int
    created_at: str = field(default_factory=now_iso)
    status: str = "running"
    done: int = 0
    message: str = ""
    error: str = ""
    items: List[ItemResult] = field(default_factory=list)
    finished_at: str = ""
    cancel_requested: bool = False


TERMINAL_ITEM_STATUSES = frozenset(
    {"ready", "skipped", "error", "applied", "restored"}
)


def _job_progress(job: Job) -> float:
    if not job.items:
        return 1.0 if job.status == "done" else 0.0
    total = 0.0
    for item in job.items:
        if item.status in TERMINAL_ITEM_STATUSES:
            total += 1.0
        else:
            total += max(0.0, min(1.0, item.progress))
    return max(0.0, min(1.0, total / len(job.items)))


@dataclass
class StudioState:
    adb: ADB
    scan: Scan
    pairs: List[LivePair]
    pairs_by_mp4: Dict[str, LivePair]
    durations: Dict[str, int]
    token: str
    port: int
    ffmpeg: str
    ffprobe: str
    max_duration: int
    include_unknown: bool = False
    workers: int = 3
    apply_workers: int = 4
    hardware_encoder: str = ""
    model: str = ""
    android: str = ""
    jobs: Dict[str, Job] = field(default_factory=dict)
    history: Dict[str, Dict] = field(default_factory=dict)
    videos: List[VideoItem] = field(default_factory=list)
    video_by_path: Dict[str, VideoItem] = field(default_factory=dict)
    video_by_id: Dict[str, VideoItem] = field(default_factory=dict)
    video_history: Dict[str, Dict] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)
    adb_lock: threading.Lock = field(default_factory=threading.Lock)
    thumb_sem: threading.BoundedSemaphore = field(
        default_factory=lambda: threading.BoundedSemaphore(3)
    )

    def dates(self) -> List[Dict]:
        buckets: Dict[str, List[LivePair]] = {}
        for pair in self.pairs:
            buckets.setdefault(pair.date, []).append(pair)
        rows = []
        for date in sorted(buckets, reverse=True):
            items = buckets[date]
            rows.append(
                {
                    "date": date,
                    "count": len(items),
                    "bytes": sum(item.size for item in items),
                    "duration_ms": sum(item.duration_ms or 0 for item in items),
                }
            )
        return rows

    def pairs_for(self, date: str) -> List[LivePair]:
        return [pair for pair in self.pairs if pair.date == date]

    def pairs_for_month(self, month: str) -> List[LivePair]:
        return [pair for pair in self.pairs if pair.month == month]

    def pairs_for_album(self, album: str) -> List[LivePair]:
        return [pair for pair in self.pairs if _album_of(pair.mp4) == album]

    def video_dates(self) -> List[Dict]:
        return dates_for(self.videos)

    def video_months(self) -> List[Dict]:
        return months_for(self.videos)

    def video_albums(self) -> List[Dict]:
        return albums_for(self.videos)

    def videos_for_date(self, date: str) -> List[VideoItem]:
        return [video for video in self.videos if video.date == date]

    def videos_for_month(self, month: str) -> List[VideoItem]:
        return [video for video in self.videos if video.month == month]

    def videos_for_album(self, album: str) -> List[VideoItem]:
        return [video for video in self.videos if video.album == album]

    def video_batch_suggestion(self) -> int:
        free = shutil.disk_usage(str(state_dir())).free
        return min(VIDEO_BATCH_SUGGESTION, max(1_000_000_000, int(free * 0.25)))

    def albums(self) -> List[Dict]:
        buckets: Dict[str, List[LivePair]] = {}
        for pair in self.pairs:
            buckets.setdefault(_album_of(pair.mp4), []).append(pair)
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

    def months(self) -> List[Dict]:
        buckets: Dict[str, List[LivePair]] = {}
        for pair in self.pairs:
            buckets.setdefault(pair.month, []).append(pair)
        rows = []
        for month in sorted(buckets, reverse=True):
            items = buckets[month]
            rows.append(
                {
                    "month": month,
                    "count": len(items),
                    "bytes": sum(item.size for item in items),
                }
            )
        return rows

    def new_job(self, kind: str, items: List[ItemResult], message: str = "") -> Job:
        with self.lock:
            if self.active_job() is not None:
                raise Busy("已有任务在运行，请等它结束")
            job = Job(id=secrets.token_hex(6), kind=kind, total=len(items), items=items, message=message)
            self.jobs[job.id] = job
            self._trim_jobs()
            return job

    def active_job(self) -> Optional[Job]:
        for job in self.jobs.values():
            if job.status == "running":
                return job
        return None

    def finish_job(self, job: Job, status: str = "done", error: str = "") -> None:
        with self.lock:
            job.status = status
            job.error = error
            job.finished_at = now_iso()

    def _trim_jobs(self) -> None:
        if len(self.jobs) <= 20:
            return
        finished = sorted(
            (job for job in self.jobs.values() if job.status != "running"),
            key=lambda item: item.created_at,
        )
        for job in finished[: max(0, len(self.jobs) - 20)]:
            self.jobs.pop(job.id, None)

    def update_scan_size(self, path: str, size: int) -> None:
        for entry in self.scan.entries:
            if entry.path == path:
                entry.size = size
                break

    def find_item(self, job_id: str, mp4: str) -> Optional[ItemResult]:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        for item in job.items:
            if item.mp4 == mp4:
                return item
        return None


def _album_of(mp4: str) -> str:
    if "/" not in mp4:
        return "/"
    return mp4.rsplit("/", 1)[0] or "/"


def _safe_sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()


def _job_dir(job: Job) -> Path:
    return JOBS_DIR / job.id


def _job_file(state: StudioState, job_id: str, relative: str) -> Optional[Path]:
    """Resolve a file inside a job directory; reject traversal."""
    job = state.jobs.get(job_id)
    if job is None or not relative:
        return None
    base = _job_dir(job).resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def _backup_file(relative: str) -> Optional[Path]:
    """Resolve a file inside the studio backup root; reject traversal."""
    if not relative:
        return None
    base = BACKUPS_DIR.resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def _video_backup_file(relative: str) -> Optional[Path]:
    if not relative:
        return None
    base = VIDEO_BACKUPS_DIR.resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def _new_video_backup_dir() -> Path:
    VIDEO_BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    base = VIDEO_BACKUPS_DIR / stamp()
    path = base
    counter = 1
    while path.exists():
        counter += 1
        path = VIDEO_BACKUPS_DIR / f"{base.name}-{counter}"
    path.mkdir(parents=True)
    return path


def _rel_to_video_backup(path: Path) -> str:
    return path.relative_to(VIDEO_BACKUPS_DIR).as_posix()


def _local_md5(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_verified(source: Path, target: Path, expected_md5: str) -> bool:
    """Back up source to target, preferring a hard link (zero extra writes).

    Job sources and backups live under the same cache root, so a hard link
    usually works. It is verified by MD5 either way. Returns True if linked.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    linked = False
    try:
        os.link(str(source), str(target))
        linked = True
    except OSError:
        shutil.copyfile(source, target)
    actual = _local_md5(target)
    if actual != expected_md5:
        target.unlink(missing_ok=True)
        raise CompressError(f"备份 MD5 校验失败: {actual} != {expected_md5}")
    return linked


def estimate_local_need(total_bytes: int) -> int:
    """Rough local space needed: pulled source + encoded output + headroom."""
    return int(total_bytes * 1.5) + 256 * 1024 * 1024


LIVE_NAME_RE = re.compile(r"IMG_(\d{8})_(\d{6})\.mp4$", re.IGNORECASE)


def _date_time_from_path(path: str) -> Tuple[str, str]:
    match = LIVE_NAME_RE.search(path)
    if not match:
        return "", ""
    day, clock = match.group(1), match.group(2)
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}", f"{clock[:2]}:{clock[2:4]}:{clock[4:6]}"


def _load_history() -> Dict[str, Dict]:
    payload = read_json(HISTORY_PATH, {})
    if not isinstance(payload, dict):
        return {}
    return {
        str(path): entry
        for path, entry in payload.items()
        if isinstance(entry, dict) and path.startswith("/sdcard/")
    }


def _save_history(history: Dict[str, Dict]) -> None:
    write_json(HISTORY_PATH, history)


def _history_entry(state: StudioState, pair: LivePair) -> Optional[Dict]:
    entry = state.history.get(pair.mp4)
    if not entry:
        return None
    if int(entry.get("compressed_size", -1)) != pair.size:
        return None
    return entry


def _history_skip_reason(entry: Optional[Dict], recompress: bool, preset) -> str:
    if not entry:
        return ""
    old_crf = entry.get("crf_used")
    old_label = f"CRF{old_crf}" if old_crf is not None else "档位未知"
    if preset.mode == "copy":
        return f"已压缩过（{old_label}）；无损重封装不会进一步减小体积"
    if not recompress:
        return f"已压缩过（{old_label}）；勾选“允许更深压缩”后再处理"
    new_crf = preset.crf
    if old_crf is not None and (new_crf is None or int(new_crf) <= int(old_crf)):
        return f"已压缩过（{old_label}），当前档位不是更深压缩"
    return ""


def _load_video_history() -> Dict[str, Dict]:
    payload = read_json(VIDEO_HISTORY_PATH, {})
    if not isinstance(payload, dict):
        return {}
    return {
        str(path): entry
        for path, entry in payload.items()
        if isinstance(entry, dict) and path.startswith("/sdcard/")
    }


def _save_video_history(history: Dict[str, Dict]) -> None:
    write_json(VIDEO_HISTORY_PATH, history)


def _video_history_entry(state: StudioState, video: VideoItem) -> Optional[Dict]:
    entry = state.video_history.get(video.path)
    if not entry:
        return None
    if int(entry.get("compressed_size", -1)) != video.size:
        return None
    return entry


def _video_payload(video: VideoItem, state: StudioState) -> Dict:
    entry = _video_history_entry(state, video)
    return {
        "id": video.media_id,
        "path": video.path,
        "size": video.size,
        "duration_ms": video.duration_ms,
        "width": video.width,
        "height": video.height,
        "date": video.date,
        "time": video.time,
        "album": video.album,
        "compressed": bool(entry),
        "history_crf": entry.get("crf_used") if entry else None,
        "history_size": entry.get("compressed_size") if entry else None,
    }


def _video_thumb_path(video: VideoItem) -> Path:
    return VIDEO_THUMBS_DIR / f"{video.media_id}-{video.size}.jpg"


def _make_video_thumbnail(state: StudioState, video: VideoItem) -> Optional[Path]:
    target = _video_thumb_path(video)
    if target.exists() and target.stat().st_size > 0:
        return target
    VIDEO_THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    uri = f"content://media/external/video/media/{video.media_id}/thumbnail"
    with state.thumb_sem:
        if target.exists() and target.stat().st_size > 0:
            return target
        temp = target.with_name(f".{target.name}.tmp")
        try:
            result = state.adb.run(
                "exec-out", "content", "read", "--uri", uri, timeout=90
            )
            data = result.stdout
            if result.returncode != 0 or not data.startswith(b"\xff\xd8"):
                return None
            temp.write_bytes(data)
            os.replace(temp, target)
            return target
        except (OSError, ADBError):
            return None
        finally:
            with contextlib.suppress(OSError):
                if temp.exists():
                    temp.unlink()



def _read_backup_session(session: Path) -> Optional[Dict]:
    manifest = session / "manifest.tsv"
    if not manifest.exists():
        return None
    entries: List[Dict] = []
    try:
        with open(manifest, "r", encoding="utf-8") as handle:
            handle.readline()
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 7:
                    continue
                mp4, jpg, original, new, old_md5, new_md5, mtime = parts[:7]
                if not mp4.startswith("/sdcard/") or not jpg.startswith("/sdcard/"):
                    continue
                try:
                    original_size = int(original)
                    new_size = int(new)
                    mtime_value = int(mtime)
                except ValueError:
                    continue
                if not re.fullmatch(r"[0-9a-f]{32}", old_md5):
                    continue
                if not re.fullmatch(r"[0-9a-f]{32}", new_md5):
                    continue
                relative = "sdcard/" + mp4[len("/sdcard/") :]
                date, time = _date_time_from_path(mp4)
                entries.append(
                    {
                        "mp4": mp4,
                        "jpg": jpg,
                        "date": date,
                        "time": time,
                        "original_size": original_size,
                        "new_size": new_size,
                        "original_md5": old_md5,
                        "new_md5": new_md5,
                        "mtime": mtime_value,
                        "backup": f"{session.name}/{relative}",
                    }
                )
    except OSError:
        return None
    if not entries:
        return None
    return {
        "session": session.name,
        "count": len(entries),
        "bytes": sum(entry["original_size"] for entry in entries),
        "restore_script": str(session / "restore.sh"),
        "entries": entries,
    }


def _delete_backup_session(session_name: str) -> int:
    session = next(
        (item for item in _backup_sessions() if item["session"] == session_name),
        None,
    )
    if session is None:
        raise BadRequest("备份批次不存在")
    path = (BACKUPS_DIR / session_name).resolve()
    base = BACKUPS_DIR.resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise BadRequest("备份路径非法") from exc
    if path.parent != base or not path.is_dir():
        raise BadRequest("备份路径非法")
    shutil.rmtree(path)
    return session["count"]


def _backup_sessions() -> List[Dict]:
    if not BACKUPS_DIR.exists():
        return []
    sessions = []
    for path in sorted(BACKUPS_DIR.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        info = _read_backup_session(path)
        if info is not None:
            sessions.append(info)
    return sessions


def _migrate_legacy_backups() -> None:
    """Move backups out of the cache directory into Application Support."""
    legacy = LEGACY_BACKUPS_DIR
    if not legacy.exists():
        return
    try:
        if legacy.resolve() == BACKUPS_DIR.resolve():
            return
    except OSError:
        return
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    for session in sorted(legacy.iterdir()):
        if not session.is_dir():
            continue
        target = BACKUPS_DIR / session.name
        if target.exists():
            continue
        with contextlib.suppress(OSError):
            shutil.move(str(session), str(target))
    with contextlib.suppress(OSError):
        legacy.rmdir()


def _new_backup_dir() -> Path:
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    base = BACKUPS_DIR / stamp()
    path = base
    counter = 1
    while path.exists():
        counter += 1
        path = BACKUPS_DIR / f"{base.name}-{counter}"
    path.mkdir(parents=True)
    return path


def _rel_to_backup(path: Path) -> str:
    return path.relative_to(BACKUPS_DIR).as_posix()


def _backup_target(backup_dir: Path, remote: str) -> Path:
    relative = remote[len("/sdcard/") :] if remote.startswith("/sdcard/") else remote.lstrip("/")
    return backup_dir / "sdcard" / relative


def _write_restore_script(backup_dir: Path, rows: List[Tuple[str, str, int]]) -> Path:
    """rows: (backup_relative, remote_path, mtime)."""
    path = backup_dir / "restore.sh"
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by klean studio: restore backed-up live-photo MP4s.",
        "set -euo pipefail",
        'ADB="${ADB:-adb}"',
        'HERE="$(cd "$(dirname "$0")" && pwd)"',
        "",
    ]
    for backup_relative, remote, mtime in rows:
        local = '"$HERE"/' + sh_quote(backup_relative)
        temp = remote.rsplit("/", 1)[0] + "/." + remote.rsplit("/", 1)[-1] + ".restore.tmp"
        lines.append(f'"$ADB" push {local} {sh_quote(temp)}')
        lines.append(f'"$ADB" shell chmod 660 {sh_quote(temp)}')
        lines.append(f'"$ADB" shell touch -d @{mtime} {sh_quote(temp)}')
        lines.append(f'"$ADB" shell mv -f {sh_quote(temp)} {sh_quote(remote)}')
        lines.append(
            '"$ADB" shell am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE '
            f"-d {sh_quote('file://' + remote)} >/dev/null"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(path, 0o755)
    return path


def _write_backup_manifest(backup_dir: Path, rows: List[List[str]]) -> Path:
    path = backup_dir / "manifest.tsv"
    header = ["mp4", "jpg", "original_size", "new_size", "original_md5", "new_md5", "mtime"]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\t".join(header) + "\n")
        for row in rows:
            handle.write("\t".join(str(cell).replace("\t", " ") for cell in row) + "\n")
    return path


def _thumb_path(pair: LivePair) -> Path:
    return THUMBS_DIR / (_safe_sha1(pair.mp4) + ".jpg")


def _make_thumbnail(state: StudioState, pair: LivePair, ffmpeg: str) -> Optional[Path]:
    target = _thumb_path(pair)
    if target.exists() and target.stat().st_size > 0:
        return target
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    with state.thumb_sem:
        if target.exists() and target.stat().st_size > 0:
            return target
        key = _safe_sha1(pair.mp4)
        source = THUMBS_DIR / f".{key}.src.jpg"
        temp = THUMBS_DIR / f".{key}.thumb.jpg"
        try:
            if not state.adb.pull(pair.jpg, str(source), timeout=180):
                return None
            made = False
            if shutil.which(ffmpeg) or os.path.exists(ffmpeg):
                result = subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-vf",
                        f"scale={THUMB_WIDTH}:-2:flags=lanczos",
                        "-q:v",
                        "4",
                        "-frames:v",
                        "1",
                        str(temp),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=120,
                )
                made = result.returncode == 0 and temp.exists()
            if not made and shutil.which("sips"):
                result = subprocess.run(
                    ["sips", "-Z", str(THUMB_WIDTH), str(source), "--out", str(temp)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=120,
                )
                made = result.returncode == 0 and temp.exists()
            if not made:
                return None
            os.replace(temp, target)
            return target
        finally:
            for path in (source, temp):
                if path.exists():
                    path.unlink()


def _push_replace(
    state: StudioState,
    local: Path,
    remote: str,
    expected_md5: str,
    mtime: int,
    mode: str = "660",
    timeout: int = 600,
) -> None:
    temp = remote.rsplit("/", 1)[0] + "/." + remote.rsplit("/", 1)[-1] + ".klean.tmp"
    quoted_temp = sh_quote(temp)
    quoted_remote = sh_quote(remote)
    if not state.adb.push(str(local), temp, timeout=timeout):
        raise CompressError(f"push 失败: {remote}")
    try:
        pushed_md5 = state.adb.remote_md5(temp)
        if pushed_md5 != expected_md5:
            raise CompressError(
                f"手机端临时文件 MD5 不一致: {pushed_md5 or '读取失败'} != {expected_md5}"
            )
        output = state.adb.sh(
            f"chmod {mode} {quoted_temp}; touch -d @{mtime} {quoted_temp}; "
            f"mv -f {quoted_temp} {quoted_remote}; "
            f"md5sum {quoted_remote}; stat -c '%a %Y' {quoted_remote}; "
            "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
            f"-d {sh_quote('file://' + remote)} >/dev/null",
            check=False,
        )
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        hash_line = next(
            (line for line in lines if re.match(r"^[0-9a-fA-F]{32}\b", line)),
            "",
        )
        stat_line = next(
            (line for line in lines if re.fullmatch(r"\d+\s+\d+", line)),
            "",
        )
        if not hash_line or not stat_line:
            raise CompressError("替换后无法读取手机文件状态")
        final_md5 = hash_line.split()[0].lower()
        if final_md5 != expected_md5:
            raise CompressError(
                f"替换后 MD5 不一致: {final_md5 or '读取失败'} != {expected_md5}"
            )
        final_mode, final_mtime = stat_line.split()
        if final_mode != mode:
            raise CompressError(f"替换后权限不一致: {final_mode} != {mode}")
        if abs(int(final_mtime) - mtime) > 1:
            raise CompressError(f"替换后 mtime 不一致: {final_mtime} != {mtime}")
    except Exception:
        state.adb.sh(f"rm -f {quoted_temp}", check=False)
        raise

def _restore_one(
    state: StudioState,
    backup_path: Path,
    remote: str,
    expected_md5: str,
    mtime: int,
) -> None:
    if not backup_path.exists():
        raise CompressError(f"备份文件不存在: {backup_path}")
    actual = _local_md5(backup_path)
    if actual != expected_md5:
        raise CompressError(f"备份文件 MD5 不一致: {actual} != {expected_md5}")
    _push_replace(state, backup_path, remote, expected_md5, mtime)


def _set_item_progress(
    state: StudioState, item: ItemResult, value: float, phase: Optional[str] = None
) -> None:
    with state.lock:
        item.progress = max(item.progress, min(1.0, value))
        if phase is not None:
            item.phase = phase


def _compress_one(
    state: StudioState,
    job: Job,
    item: ItemResult,
    preset,
    speed: str,
    compute_ssim: bool,
    src_dir: Path,
    out_dir: Path,
    directory: Path,
) -> None:
    started = time.monotonic()
    if job.cancel_requested:
        with state.lock:
            item.status = "skipped"
            item.reason = "用户已停止任务"
            item.progress = 1.0
            job.done += 1
        return
    with state.lock:
        if item.status == "skipped":
            item.progress = 1.0
            job.done += 1
            return
        item.status = "running"
        item.progress = max(item.progress, 0.02)
        item.phase = "拉取中"
    try:
        pair = state.pairs_by_mp4.get(item.mp4)
        if pair is None:
            raise BadRequest("不在实况照片配对清单中")
        if pair.duration_ms is not None and pair.duration_ms > state.max_duration:
            raise CompressError(f"时长 {pair.duration_ms}ms 超过上限，已跳过")
        free = shutil.disk_usage(str(state_dir())).free
        if free < estimate_local_need(pair.size):
            raise CompressError(
                f"本机空间不足：需要约 {human(estimate_local_need(pair.size))}，"
                f"当前可用 {human(free)}；请缩小批次"
            )
        local_src = src_dir / (_safe_sha1(pair.mp4)[:12] + "-" + pair.mp4.rsplit("/", 1)[-1])
        with state.adb_lock:
            probe = state.adb.sh(
                f"stat -c '%s %Y' {sh_quote(pair.mp4)}; "
                f"[ -f {sh_quote(pair.jpg)} ] && echo JPG_OK",
                check=False,
            )
            lines = [line.strip() for line in probe.splitlines() if line.strip()]
            if "JPG_OK" not in lines:
                raise CompressError("同名 JPG 不存在，已跳过")
            stat_line = next((line for line in lines if line != "JPG_OK"), "")
            parts = stat_line.split()
            if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
                raise CompressError("无法读取手机文件信息，已跳过")
            remote_size = int(parts[0])
            remote_mtime = int(parts[1])
            state.adb.pull(pair.mp4, str(local_src))
        local_size = local_src.stat().st_size if local_src.exists() else 0
        if not local_size:
            raise CompressError("adb pull 失败")
        if remote_size != local_size:
            raise CompressError(f"拉取大小不一致 {local_size} != {remote_size}")
        original_md5 = _local_md5(local_src)
        local_out = out_dir / (_safe_sha1(pair.mp4)[:12] + "-" + pair.mp4.rsplit("/", 1)[-1])
        _set_item_progress(state, item, 0.08, "编码中")
        result = compress_file(
            str(local_src),
            str(local_out),
            preset,
            ffmpeg=state.ffmpeg,
            ffprobe=state.ffprobe,
            compute_ssim=compute_ssim,
            speed=speed,
            on_progress=lambda fraction: _set_item_progress(
                state, item, 0.08 + 0.84 * fraction
            ),
        )
        _set_item_progress(state, item, 0.94, "校验中")
        compressed_md5 = _local_md5(local_out)
        with state.lock:
            item.original_size = result.original_size
            item.compressed_size = result.compressed_size
            item.ssim = result.ssim
            item.original_md5 = original_md5
            item.compressed_md5 = compressed_md5
            item.output = local_out.relative_to(directory).as_posix()
            item.source = local_src.relative_to(directory).as_posix()
            item.mtime = remote_mtime
            item.duration_ms = pair.duration_ms
            item.crf_used = result.crf_used
            item.speed_used = result.speed_used
            item.status = "ready"
            item.progress = 1.0
            item.phase = ""
            reasons = []
            if result.fallback_note:
                reasons.append(result.fallback_note)
            if result.audio_reencoded:
                reasons.append("音频已重编码")
            item.reason = "；".join(reasons)
    except (HdrUnsupported, UnsupportedFormat, NoVivoUuid, NoBenefit) as exc:
        with state.lock:
            item.status = "skipped"
            item.reason = str(exc)
            item.progress = 1.0
    except (BadRequest, CompressError, ADBError, OSError, ValueError) as exc:
        with state.lock:
            item.status = "error"
            item.reason = str(exc)
            item.progress = 1.0
    except Exception as exc:  # keep one bad file from aborting a batch
        with state.lock:
            item.status = "error"
            item.reason = f"{type(exc).__name__}: {exc}"
            item.progress = 1.0
    finally:
        with state.lock:
            item.elapsed_ms = int((time.monotonic() - started) * 1000)
            if item.status in TERMINAL_ITEM_STATUSES:
                item.progress = 1.0
            job.done += 1


def _compress_job(
    state: StudioState,
    job: Job,
    preset,
    speed: str = "veryfast",
    compute_ssim: bool = False,
) -> None:
    directory = _job_dir(job)
    src_dir = directory / "src"
    out_dir = directory / "out"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not job.items:
        return
    workers = max(1, min(state.workers, len(job.items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _compress_one,
                state,
                job,
                item,
                preset,
                speed,
                compute_ssim,
                src_dir,
                out_dir,
                directory,
            )
            for item in job.items
        ]
        for future in as_completed(futures):
            with contextlib.suppress(Exception):
                future.result()
    if job.cancel_requested:
        job.message = "已停止；已完成的部分仍可应用"


def _apply_one(
    state: StudioState,
    job: Job,
    source_job: Job,
    item: ItemResult,
    backup_dir: Path,
    manifest_rows: List[List[str]],
    restore_rows: List[Tuple[str, str, int]],
) -> None:
    started = time.monotonic()
    with state.lock:
        item.status = "running"
        item.progress = max(item.progress, 0.02)
        item.phase = "校验中"
    try:
        src = state.find_item(source_job.id, item.mp4)
        if src is None or src.status not in ("ready", "applied"):
            raise CompressError("压缩结果不可用，请重新压缩")
        output = _job_file(state, source_job.id, src.output)
        if output is None or not output.exists():
            raise CompressError("本地压缩文件不存在")
        if _local_md5(output) != src.compressed_md5:
            raise CompressError("本地压缩文件 MD5 已变化，请重新压缩")
        pair = state.pairs_by_mp4.get(item.mp4)
        if pair is None:
            raise CompressError("不在实况照片配对清单中")
        probe = state.adb.sh(
            f"md5sum {sh_quote(pair.mp4)}; "
            f"stat -c '%Y %a' {sh_quote(pair.mp4)}; "
            f"[ -f {sh_quote(pair.jpg)} ] && echo JPG_OK",
            check=False,
        )
        lines = [line.strip() for line in probe.splitlines() if line.strip()]
        if "JPG_OK" not in lines:
            raise CompressError("同名 JPG 不存在，已跳过")
        hash_line = next(
            (line for line in lines if re.match(r"^[0-9a-fA-F]{32}\b", line)),
            "",
        )
        stat_line = next(
            (line for line in lines if re.fullmatch(r"\d+\s+\d+", line)),
            "",
        )
        if not hash_line or not stat_line:
            raise CompressError("无法读取手机文件信息，已跳过")
        remote_md5 = hash_line.split()[0].lower()
        stat_parts = stat_line.split()
        if remote_md5 != src.original_md5:
            raise CompressError(
                f"手机文件已变化（{remote_md5 or '读取失败'}），已跳过"
            )
        mtime = int(stat_parts[0]) or src.mtime
        mode = stat_parts[1] or "660"
        _set_item_progress(state, item, 0.12, "备份中")
        source_local = _job_file(state, source_job.id, src.source)
        if source_local is None or not source_local.exists():
            raise CompressError("本地原始备份副本丢失，已跳过")
        target = _backup_target(backup_dir, pair.mp4)
        _backup_verified(source_local, target, src.original_md5)
        _set_item_progress(state, item, 0.25, "传输中")
        with state.lock:
            item.backup = _rel_to_backup(target)
            item.original_size = src.original_size
            item.compressed_size = src.compressed_size
            item.ssim = src.ssim
            item.original_md5 = src.original_md5
            item.compressed_md5 = src.compressed_md5
            item.mtime = mtime
            item.duration_ms = pair.duration_ms
        try:
            _push_replace(
                state,
                output,
                pair.mp4,
                src.compressed_md5,
                mtime,
                mode=mode,
            )
            _set_item_progress(state, item, 0.92, "校验中")
            # The backup holds the only needed copy now; the job source is
            # either a hard link to it or a redundant copy. The encoded
            # output also lives on the phone, so drop both to save space.
            with contextlib.suppress(OSError):
                source_local.unlink()
            with contextlib.suppress(OSError):
                output.unlink()
        except Exception:
            try:
                _restore_one(state, target, pair.mp4, src.original_md5, mtime)
            except Exception as restore_error:
                raise CompressError(
                    f"替换失败且自动恢复失败: {restore_error}"
                ) from restore_error
            raise
        with state.lock:
            state.update_scan_size(pair.mp4, src.compressed_size)
            pair.size = src.compressed_size
            item.status = "applied"
            item.progress = 1.0
            item.phase = ""
            src.status = "applied"
            src.backup = item.backup
            manifest_rows.append(
                [
                    pair.mp4,
                    pair.jpg,
                    src.original_size,
                    src.compressed_size,
                    src.original_md5,
                    src.compressed_md5,
                    mtime,
                ]
            )
            restore_rows.append(
                (target.relative_to(backup_dir).as_posix(), pair.mp4, mtime)
            )
            if src.crf_used is not None:
                state.history[item.mp4] = {
                    "original_md5": src.original_md5,
                    "compressed_md5": src.compressed_md5,
                    "original_size": src.original_size,
                    "compressed_size": src.compressed_size,
                    "crf_used": src.crf_used,
                    "speed_used": src.speed_used,
                    "applied_at": now_iso(),
                    "backup": item.backup,
                }
    except (CompressError, ADBError, OSError, ValueError) as exc:
        with state.lock:
            item.status = "error"
            item.reason = str(exc)
            item.progress = 1.0
    except Exception as exc:  # keep one bad file from aborting a batch
        with state.lock:
            item.status = "error"
            item.reason = f"{type(exc).__name__}: {exc}"
            item.progress = 1.0
    finally:
        with state.lock:
            item.elapsed_ms = int((time.monotonic() - started) * 1000)
            if item.status in TERMINAL_ITEM_STATUSES:
                item.progress = 1.0
            job.done += 1


def _apply_job(state: StudioState, job: Job, source_job: Job) -> None:
    backup_dir = _new_backup_dir()
    manifest_rows: List[List[str]] = []
    restore_rows: List[Tuple[str, str, int]] = []
    if job.items:
        workers = max(1, min(state.apply_workers, len(job.items)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    _apply_one,
                    state,
                    job,
                    source_job,
                    item,
                    backup_dir,
                    manifest_rows,
                    restore_rows,
                )
                for item in job.items
            ]
            for future in as_completed(futures):
                with contextlib.suppress(Exception):
                    future.result()
    if manifest_rows:
        _write_backup_manifest(backup_dir, manifest_rows)
        _write_restore_script(backup_dir, restore_rows)
        save_scan(state.scan)
        with state.lock:
            history_snapshot = dict(state.history)
        _save_history(history_snapshot)
        job.message = f"已应用 {len(manifest_rows)} 个；备份目录 {backup_dir}"
    else:
        with contextlib.suppress(OSError):
            backup_dir.rmdir()


def _video_compress_one(
    state: StudioState,
    job: Job,
    item: ItemResult,
    preset,
    src_dir: Path,
    out_dir: Path,
    directory: Path,
) -> None:
    started = time.monotonic()
    if job.cancel_requested:
        with state.lock:
            item.status = "skipped"
            item.reason = "用户已停止任务"
            item.progress = 1.0
            job.done += 1
        return
    with state.lock:
        if item.status == "skipped":
            item.progress = 1.0
            job.done += 1
            return
        item.status = "running"
        item.progress = max(item.progress, 0.02)
        item.phase = "拉取中"
    try:
        video = state.video_by_path.get(item.mp4)
        if video is None:
            raise BadRequest("不在视频清单中")
        free = shutil.disk_usage(str(state_dir())).free
        needed = estimate_local_need(video.size)
        if free < needed:
            raise CompressError(
                f"本机空间不足：需要约 {human(needed)}，当前可用 {human(free)}；请缩小批次"
            )
        local_src = src_dir / (_safe_sha1(video.path)[:12] + "-" + video.path.rsplit("/", 1)[-1])
        local_out = out_dir / (_safe_sha1(video.path)[:12] + "-" + video.path.rsplit("/", 1)[-1])
        with state.adb_lock:
            probe = state.adb.sh(
                f"stat -c '%s %Y' {sh_quote(video.path)}; "
                f"[ -f {sh_quote(video.path)} ] && echo FILE_OK",
                check=False,
            )
            lines = [line.strip() for line in probe.splitlines() if line.strip()]
            if "FILE_OK" not in lines:
                raise CompressError("手机上的视频不存在")
            stat_line = next((line for line in lines if line != "FILE_OK"), "")
            parts = stat_line.split()
            if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
                raise CompressError("无法读取视频信息")
            remote_size = int(parts[0])
            remote_mtime = int(parts[1])
            if not state.adb.pull(video.path, str(local_src), timeout=VIDEO_TIMEOUT):
                raise CompressError("adb pull 失败")
        local_size = local_src.stat().st_size if local_src.exists() else 0
        if not local_size:
            raise CompressError("adb pull 失败")
        if remote_size != local_size:
            raise CompressError(f"拉取大小不一致 {local_size} != {remote_size}")
        original_md5 = _local_md5(local_src)
        _set_item_progress(state, item, 0.10, "编码中")
        result = compress_regular_video(
            str(local_src),
            str(local_out),
            preset,
            ffmpeg=state.ffmpeg,
            ffprobe=state.ffprobe,
            on_progress=lambda fraction: _set_item_progress(
                state, item, 0.10 + 0.84 * fraction
            ),
        )
        _set_item_progress(state, item, 0.94, "校验中")
        compressed_md5 = _local_md5(local_out)
        with state.lock:
            item.original_size = result.original_size
            item.compressed_size = result.compressed_size
            item.original_md5 = original_md5
            item.compressed_md5 = compressed_md5
            item.output = local_out.relative_to(directory).as_posix()
            item.source = local_src.relative_to(directory).as_posix()
            item.mtime = remote_mtime
            item.duration_ms = video.duration_ms
            item.crf_used = result.crf
            item.speed_used = result.speed_used
            item.status = "ready"
            item.progress = 1.0
            item.phase = ""
            item.reason = "音频已重编码" if result.audio_reencoded else ""
    except (HdrUnsupported, UnsupportedFormat, NoBenefit) as exc:
        with state.lock:
            item.status = "skipped"
            item.reason = str(exc)
            item.progress = 1.0
    except (BadRequest, CompressError, ADBError, OSError, ValueError) as exc:
        with state.lock:
            item.status = "error"
            item.reason = str(exc)
            item.progress = 1.0
    except Exception as exc:
        with state.lock:
            item.status = "error"
            item.reason = f"{type(exc).__name__}: {exc}"
            item.progress = 1.0
    finally:
        with state.lock:
            item.elapsed_ms = int((time.monotonic() - started) * 1000)
            job.done += 1


def _video_compress_job(state: StudioState, job: Job, preset) -> None:
    directory = _job_dir(job)
    src_dir = directory / "src"
    out_dir = directory / "out"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not job.items:
        return
    workers = max(1, min(VIDEO_WORKERS, len(job.items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _video_compress_one,
                state,
                job,
                item,
                preset,
                src_dir,
                out_dir,
                directory,
            )
            for item in job.items
        ]
        for future in as_completed(futures):
            with contextlib.suppress(Exception):
                future.result()
    if job.cancel_requested:
        job.message = "已停止；已完成的部分仍可应用"


def _video_apply_one(
    state: StudioState,
    job: Job,
    source_job: Job,
    item: ItemResult,
    backup_dir: Path,
    manifest_rows: List[List[str]],
    restore_rows: List[Tuple[str, str, int]],
) -> None:
    started = time.monotonic()
    with state.lock:
        item.status = "running"
        item.progress = max(item.progress, 0.02)
        item.phase = "校验中"
    try:
        src = state.find_item(source_job.id, item.mp4)
        if src is None or src.status != "ready":
            raise CompressError("压缩结果不可用，请重新压缩")
        video = state.video_by_path.get(item.mp4)
        if video is None:
            raise CompressError("不在视频清单中")
        output = _job_file(state, source_job.id, src.output)
        if output is None or not output.exists():
            raise CompressError("本地压缩文件不存在")
        if _local_md5(output) != src.compressed_md5:
            raise CompressError("本地压缩文件 MD5 已变化，请重新压缩")
        probe = state.adb.sh(
            f"md5sum {sh_quote(video.path)}; "
            f"stat -c '%Y %a' {sh_quote(video.path)}; "
            f"[ -f {sh_quote(video.path)} ] && echo FILE_OK",
            check=False,
        )
        lines = [line.strip() for line in probe.splitlines() if line.strip()]
        if "FILE_OK" not in lines:
            raise CompressError("手机上的视频不存在")
        hash_line = next(
            (line for line in lines if re.match(r"^[0-9a-fA-F]{32}\b", line)), ""
        )
        stat_line = next(
            (line for line in lines if re.fullmatch(r"\d+\s+\d+", line)), ""
        )
        if not hash_line or not stat_line:
            raise CompressError("无法读取手机视频信息")
        remote_md5 = hash_line.split()[0].lower()
        if remote_md5 != src.original_md5:
            raise CompressError("手机视频已变化，已跳过")
        stat_parts = stat_line.split()
        mtime = int(stat_parts[0]) or src.mtime
        # sdcard FUSE may report 770 but forces 660 after push; use the same
        # media-file mode as the live-photo pipeline instead of preserving it.
        mode = "660"
        _set_item_progress(state, item, 0.12, "备份中")
        source_local = _job_file(state, source_job.id, src.source)
        if source_local is None or not source_local.exists():
            raise CompressError("本地原始视频副本丢失，已跳过")
        target = _backup_target(backup_dir, video.path)
        _backup_verified(source_local, target, src.original_md5)
        item.backup = _rel_to_video_backup(target)
        _set_item_progress(state, item, 0.25, "传输中")
        try:
            _push_replace(
                state, output, video.path, src.compressed_md5, mtime, mode=mode
            )
            _set_item_progress(state, item, 0.92, "校验中")
            with contextlib.suppress(OSError):
                source_local.unlink()
            with contextlib.suppress(OSError):
                output.unlink()
        except Exception:
            try:
                _restore_one(state, target, video.path, src.original_md5, mtime)
            except Exception as restore_error:
                raise CompressError(
                    f"替换失败且自动恢复失败: {restore_error}"
                ) from restore_error
            raise
        with state.lock:
            state.update_scan_size(video.path, src.compressed_size)
            video.size = src.compressed_size
            item.original_size = src.original_size
            item.compressed_size = src.compressed_size
            item.original_md5 = src.original_md5
            item.compressed_md5 = src.compressed_md5
            item.mtime = mtime
            item.duration_ms = video.duration_ms
            item.crf_used = src.crf_used
            item.speed_used = src.speed_used
            item.status = "applied"
            item.progress = 1.0
            item.phase = ""
            src.status = "applied"
            src.backup = item.backup
            manifest_rows.append(
                [
                    video.path,
                    "-",
                    src.original_size,
                    src.compressed_size,
                    src.original_md5,
                    src.compressed_md5,
                    mtime,
                ]
            )
            restore_rows.append(
                (target.relative_to(backup_dir).as_posix(), video.path, mtime)
            )
            state.video_history[video.path] = {
                "original_md5": src.original_md5,
                "compressed_md5": src.compressed_md5,
                "original_size": src.original_size,
                "compressed_size": src.compressed_size,
                "crf_used": src.crf_used,
                "speed_used": src.speed_used,
                "applied_at": now_iso(),
                "backup": item.backup,
            }
    except (CompressError, ADBError, OSError, ValueError) as exc:
        with state.lock:
            item.status = "error"
            item.reason = str(exc)
            item.progress = 1.0
    except Exception as exc:
        with state.lock:
            item.status = "error"
            item.reason = f"{type(exc).__name__}: {exc}"
            item.progress = 1.0
    finally:
        with state.lock:
            item.elapsed_ms = int((time.monotonic() - started) * 1000)
            job.done += 1


def _video_apply_job(state: StudioState, job: Job, source_job: Job) -> None:
    backup_dir = _new_video_backup_dir()
    manifest_rows: List[List[str]] = []
    restore_rows: List[Tuple[str, str, int]] = []
    if job.items:
        workers = max(1, min(VIDEO_WORKERS, len(job.items)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    _video_apply_one,
                    state,
                    job,
                    source_job,
                    item,
                    backup_dir,
                    manifest_rows,
                    restore_rows,
                )
                for item in job.items
            ]
            for future in as_completed(futures):
                with contextlib.suppress(Exception):
                    future.result()
    if manifest_rows:
        _write_backup_manifest(backup_dir, manifest_rows)
        _write_restore_script(backup_dir, restore_rows)
        save_scan(state.scan)
        with state.lock:
            history_snapshot = dict(state.video_history)
        _save_video_history(history_snapshot)
        job.message = f"已应用 {len(manifest_rows)} 个视频；备份目录 {backup_dir}"
    else:
        with contextlib.suppress(OSError):
            backup_dir.rmdir()


def _video_restore_job(state: StudioState, job: Job, source_job: Job) -> None:
    for item in job.items:
        with state.lock:
            item.status = "running"
            item.progress = 0.1
            item.phase = "恢复中"
        try:
            src = state.find_item(source_job.id, item.mp4)
            if src is None or not src.backup:
                raise CompressError("找不到该视频的备份记录")
            backup_path = _video_backup_file(src.backup)
            if backup_path is None or not backup_path.exists():
                raise CompressError("备份文件不在视频备份目录内或已丢失")
            video = state.video_by_path.get(item.mp4)
            if video is None:
                raise CompressError("不在视频清单中")
            probe = state.adb.sh(
                f"md5sum {sh_quote(video.path)}; [ -f {sh_quote(video.path)} ] && echo FILE_OK",
                check=False,
            )
            lines = [line.strip() for line in probe.splitlines() if line.strip()]
            hash_line = next(
                (line for line in lines if re.match(r"^[0-9a-fA-F]{32}\b", line)), ""
            )
            if "FILE_OK" not in lines or not hash_line:
                raise CompressError("手机视频不存在")
            if hash_line.split()[0].lower() != src.compressed_md5:
                raise CompressError("手机视频已被外部修改，未恢复")
            _restore_one(state, backup_path, video.path, src.original_md5, src.mtime)
            with state.lock:
                state.update_scan_size(video.path, src.original_size)
                video.size = src.original_size
                item.original_size = src.original_size
                item.compressed_size = src.compressed_size
                item.backup = src.backup
                item.mtime = src.mtime
                item.status = "restored"
                item.progress = 1.0
                item.phase = ""
                src.status = "restored"
                state.video_history.pop(video.path, None)
        except (CompressError, ADBError, OSError, ValueError) as exc:
            with state.lock:
                item.status = "error"
                item.reason = str(exc)
                item.progress = 1.0
        except Exception as exc:
            with state.lock:
                item.status = "error"
                item.reason = f"{type(exc).__name__}: {exc}"
                item.progress = 1.0
        finally:
            with state.lock:
                job.done += 1
    save_scan(state.scan)
    with state.lock:
        history_snapshot = dict(state.video_history)
    _save_video_history(history_snapshot)


def _restore_job(state: StudioState, job: Job, source_job: Job) -> None:
    for item in job.items:
        with state.lock:
            item.status = "running"
        try:
            src = state.find_item(source_job.id, item.mp4)
            if src is None or not src.backup:
                raise CompressError("找不到该文件的备份记录")
            backup_path = _backup_file(src.backup)
            if backup_path is None or not backup_path.exists():
                raise CompressError("备份文件不在 Studio 备份目录内或已丢失")
            pair = state.pairs_by_mp4.get(item.mp4)
            if pair is None:
                raise CompressError("不在实况照片配对清单中")
            mtime = src.mtime or 0
            _restore_one(state, backup_path, pair.mp4, src.original_md5, mtime)
            with state.lock:
                state.update_scan_size(pair.mp4, src.original_size)
                pair.size = src.original_size
                item.original_size = src.original_size
                item.compressed_size = src.compressed_size
                item.backup = src.backup
                item.mtime = mtime
                item.status = "restored"
                src.status = "restored"
                state.history.pop(pair.mp4, None)
        except (CompressError, ADBError, OSError, ValueError) as exc:
            with state.lock:
                item.status = "error"
                item.reason = str(exc)
        except Exception as exc:  # keep one bad file from aborting a batch
            with state.lock:
                item.status = "error"
                item.reason = f"{type(exc).__name__}: {exc}"
        finally:
            with state.lock:
                job.done += 1
    save_scan(state.scan)
    with state.lock:
        history_snapshot = dict(state.history)
    _save_history(history_snapshot)


def _restore_backup_job(state: StudioState, job: Job, session_name: str) -> None:
    """Restore directly from a persisted backup session (survives restarts)."""
    session = next(
        (item for item in _backup_sessions() if item["session"] == session_name),
        None,
    )
    if session is None:
        raise CompressError("备份批次不存在")
    entries = {entry["mp4"]: entry for entry in session["entries"]}
    restored = 0
    for item in job.items:
        with state.lock:
            item.status = "running"
        try:
            entry = entries.get(item.mp4)
            if entry is None:
                raise CompressError("该文件不在备份清单中")
            backup_path = _backup_file(entry["backup"])
            if backup_path is None or not backup_path.exists():
                raise CompressError("备份文件不存在或超出备份目录")
            with state.adb_lock:
                probe = state.adb.sh(
                    f"[ -f {sh_quote(item.mp4)} ] && echo MP4_OK; "
                    f"[ -f {sh_quote(item.jpg)} ] && echo JPG_OK; "
                    f"md5sum {sh_quote(item.mp4)}",
                    check=False,
                )
                lines = [line.strip() for line in probe.splitlines() if line.strip()]
                if "MP4_OK" not in lines:
                    raise CompressError("手机上的目标 MP4 已不存在，跳过")
                if "JPG_OK" not in lines:
                    raise CompressError("同名 JPG 不存在，跳过")
                hash_line = next(
                    (line for line in lines if re.match(r"^[0-9a-fA-F]{32}\b", line)),
                    "",
                )
                if not hash_line:
                    raise CompressError("无法读取手机文件 MD5，跳过")
                current = hash_line.split()[0].lower()
            if current == entry["original_md5"]:
                item.reason = "手机已经是原始文件"
            elif current == entry["new_md5"]:
                _restore_one(
                    state,
                    backup_path,
                    item.mp4,
                    entry["original_md5"],
                    entry["mtime"],
                )
                restored += 1
            else:
                raise CompressError("手机文件已变化，不是本工具生成的压缩版，跳过")
            pair = state.pairs_by_mp4.get(item.mp4)
            with state.lock:
                item.original_size = entry["original_size"]
                item.compressed_size = entry["new_size"]
                item.original_md5 = entry["original_md5"]
                item.compressed_md5 = entry["new_md5"]
                item.mtime = entry["mtime"]
                item.backup = entry["backup"]
                item.status = "restored"
                state.history.pop(item.mp4, None)
                if pair is not None:
                    state.update_scan_size(item.mp4, entry["original_size"])
                    pair.size = entry["original_size"]
        except (CompressError, ADBError, OSError, ValueError) as exc:
            with state.lock:
                item.status = "error"
                item.reason = str(exc)
        except Exception as exc:  # keep one bad file from aborting a batch
            with state.lock:
                item.status = "error"
                item.reason = f"{type(exc).__name__}: {exc}"
        finally:
            with state.lock:
                job.done += 1
    save_scan(state.scan)
    with state.lock:
        history_snapshot = dict(state.history)
    _save_history(history_snapshot)
    job.message = f"已从备份恢复 {restored} 个"


def _scan_job(state: StudioState, job: Job) -> None:
    with state.lock:
        job.message = "正在扫描 /sdcard..."
    scan = scan_device(state.adb, "/sdcard")
    with state.lock:
        job.message = "正在读取 MediaStore 时长..."
    durations = load_durations(state.adb)
    pairs, _, _ = find_pairs(
        scan.entries,
        durations,
        max_duration_ms=state.max_duration,
        include_unknown=state.include_unknown,
    )
    if not durations:
        with state.lock:
            job.message = "MediaStore 时长不可用，仅按命名匹配"
    save_scan(scan)
    with state.lock:
        state.scan = scan
        state.durations = durations
        state.pairs = pairs
        state.pairs_by_mp4 = {pair.mp4: pair for pair in pairs}
        job.done = 1
        job.message = f"扫描完成：{len(pairs):,} 个实况照片"


def _start_thread(state: StudioState, job: Job, func: Callable[[], None]) -> None:
    def runner() -> None:
        try:
            func()
        except Exception as exc:  # keep the server alive; report to UI
            state.finish_job(job, "error", f"{type(exc).__name__}: {exc}")
        else:
            state.finish_job(job, "done")

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


def _item_payload(item: ItemResult) -> Dict:
    return {
        "mp4": item.mp4,
        "jpg": item.jpg,
        "date": item.date,
        "time": item.time,
        "status": item.status,
        "original_size": item.original_size,
        "compressed_size": item.compressed_size,
        "saved_bytes": max(0, item.original_size - item.compressed_size),
        "ratio": (
            (item.compressed_size / item.original_size) if item.original_size else None
        ),
        "ssim": item.ssim,
        "reason": item.reason,
        "original_md5": item.original_md5,
        "compressed_md5": item.compressed_md5,
        "backup": item.backup,
        "mtime": item.mtime,
        "duration_ms": item.duration_ms,
        "elapsed_ms": item.elapsed_ms,
        "crf_used": item.crf_used,
        "speed_used": item.speed_used,
        "progress": round(max(0.0, min(1.0, item.progress)), 4),
        "phase": item.phase,
    }


def _job_payload(job: Job) -> Dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "total": job.total,
        "done": job.done,
        "progress": round(_job_progress(job), 4),
        "message": job.message,
        "error": job.error,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "cancel_requested": job.cancel_requested,
        "items": [_item_payload(item) for item in job.items],
    }


def _photo_payload(pair: LivePair, state: Optional[StudioState] = None) -> Dict:
    entry = _history_entry(state, pair) if state is not None else None
    return {
        "mp4": pair.mp4,
        "jpg": pair.jpg,
        "date": pair.date,
        "time": pair.time,
        "size": pair.size,
        "duration_ms": pair.duration_ms,
        "unknown_duration": pair.duration_ms is None,
        "album": _album_of(pair.mp4),
        "compressed": bool(entry),
        "history_crf": entry.get("crf_used") if entry else None,
        "history_size": entry.get("compressed_size") if entry else None,
    }


def _presets_payload() -> List[Dict]:
    rows = [PRESETS[key] for key in PRESET_ORDER]
    return [
        {
            "key": preset.key,
            "label": preset.label,
            "crf": preset.crf,
            "mode": preset.mode,
            "description": preset.description,
        }
        for preset in rows
    ]


def _tool_version(tool: str, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            [tool, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    first = result.stdout.decode("utf-8", "replace").splitlines()
    return first[0] if first else ""


def _parse_json_body(handler: "StudioHandler") -> Dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > MAX_BODY:
        raise BadRequest("请求体过大")
    raw = handler.rfile.read(length)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadRequest("请求体不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise BadRequest("请求体必须是 JSON 对象")
    return payload


def _string_list(payload: Dict, key: str = "paths") -> List[str]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BadRequest(f"{key} 必须是字符串数组")
    seen = set()
    result = []
    for item in value:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


class StudioHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Klean-Studio"

    @property
    def state(self) -> StudioState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # keep stdout clean
        return

    # ------------------------------------------------------------------ helpers
    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        parsed = urllib.parse.urlsplit("//" + host)
        return (parsed.hostname or "") in ALLOWED_HOSTS

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme not in ("http", "https"):
            return False
        if (parsed.hostname or "") not in ALLOWED_HOSTS:
            return False
        try:
            port = parsed.port
        except ValueError:
            return False
        return port in (None, self.state.port)

    def _query(self) -> Dict[str, List[str]]:
        parsed = urllib.parse.urlsplit(self.path)
        return urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    def _token_ok(self, query: Optional[Dict[str, List[str]]] = None) -> bool:
        token = self.headers.get("X-Klean-Token", "")
        if not token:
            query = query if query is not None else self._query()
            token = (query.get("token") or [""])[0]
        return bool(token) and secrets.compare_digest(token, self.state.token)

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        status: int = 200,
        cache_control: str = "no-store",
        extra: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_json(self, payload, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status)

    def _error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status)

    def _send_file(
        self,
        path: Path,
        content_type: str,
        cache_control: str = "no-store",
    ) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self._error(404, "文件不存在")
            return
        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range", "")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if match:
                if not match.group(1) and match.group(2):
                    suffix = min(int(match.group(2)), size)
                    start, end = size - suffix, size - 1
                else:
                    if match.group(1):
                        start = int(match.group(1))
                    if match.group(2):
                        end = int(match.group(2))
                    elif match.group(1):
                        end = size - 1
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _stream_job(self, job_id: str) -> None:
        job = self.state.jobs.get(job_id)
        if job is None:
            self._error(404, "任务不存在")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        if self.command == "HEAD":
            return
        last = None
        last_sent = 0.0
        while True:
            with self.state.lock:
                payload = {
                    "id": job.id,
                    "status": job.status,
                    "done": job.done,
                    "total": job.total,
                    "progress": round(_job_progress(job), 4),
                    "message": job.message,
                    "error": job.error,
                    "finished_at": job.finished_at,
                }
            signature = (
                payload["status"],
                payload["done"],
                payload["progress"],
                payload["message"],
                payload["error"],
            )
            now = time.monotonic()
            if signature != last or now - last_sent >= 1.0:
                data = (
                    "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                ).encode("utf-8")
                try:
                    self.wfile.write(data)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                last = signature
                last_sent = now
            if payload["status"] != "running":
                return
            time.sleep(0.1)

    def _serve_phone_video(self, remote: str) -> None:
        pair = self.state.pairs_by_mp4.get(remote)
        if pair is None:
            raise BadRequest("不在实况照片配对清单中")
        cache_dir = state_dir() / "studio" / "videos"
        cache_dir.mkdir(parents=True, exist_ok=True)
        name = f"{_safe_sha1(remote)[:16]}-{pair.size}.mp4"
        target = cache_dir / name
        if not target.exists():
            temp = cache_dir / f".{name}.part-{secrets.token_hex(3)}"
            try:
                with self.state.adb_lock:
                    pulled = self.state.adb.pull(remote, str(temp))
                if not pulled:
                    raise CompressError("从手机拉取视频失败")
                os.replace(temp, target)
                cache_limit = 2 * 1024 * 1024 * 1024
                try:
                    cached = sorted(
                        (
                            entry
                            for entry in cache_dir.iterdir()
                            if entry.suffix == ".mp4" and not entry.name.startswith(".")
                        ),
                        key=lambda entry: entry.stat().st_mtime,
                    )
                except OSError:
                    cached = []
                total_bytes = 0
                for entry in cached:
                    with contextlib.suppress(OSError):
                        total_bytes += entry.stat().st_size
                for entry in cached:
                    if total_bytes <= cache_limit:
                        break
                    with contextlib.suppress(OSError):
                        size = entry.stat().st_size
                        entry.unlink()
                        total_bytes -= size
            finally:
                with contextlib.suppress(OSError):
                    if temp.exists():
                        temp.unlink()
        self._send_file(target, "video/mp4", "private, max-age=600")

    # --------------------------------------------------------------------- GET
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._host_ok():
            self._error(403, "Host 校验失败")
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/favicon.ico":
            self._send_bytes(b"", "image/x-icon", 204)
            return
        if path in ("/", "/start"):
            if not self._token_ok():
                self._error(403, "缺少或错误的访问 token")
                return
            self._serve_page("start.html")
            return
        if path in ("/studio", "/index.html"):
            if not self._token_ok():
                self._error(403, "缺少或错误的访问 token")
                return
            self._serve_page("index.html")
            return
        if path == "/videos":
            if not self._token_ok():
                self._error(403, "缺少或错误的访问 token")
                return
            self._serve_page("video.html")
            return
        if path in (
            "/static/app.js",
            "/static/style.css",
            "/static/video.js",
            "/static/video.css",
        ):
            self._serve_static(path.rsplit("/", 1)[-1])
            return
        if path.startswith("/api/"):
            if not self._token_ok():
                self._error(403, "缺少或错误的访问 token")
                return
            try:
                self._api_get(path)
            except (BadRequest, ValueError) as exc:
                self._error(400, str(exc))
            except Exception as exc:
                self._error(500, f"{type(exc).__name__}: {exc}")
            return
        self._error(404, "未找到")

    def _serve_index(self) -> None:
        self._serve_page("index.html")

    def _serve_page(self, name: str) -> None:
        index = WEB_DIR / name
        if not index.exists():
            self._error(500, "前端资源缺失")
            return
        text = index.read_text(encoding="utf-8")
        text = text.replace("__KLEAN_TOKEN__", html.escape(self.state.token, quote=True))
        data = text.encode("utf-8")
        self._send_bytes(data, "text/html; charset=utf-8")

    def _serve_static(self, name: str) -> None:
        content_types = {
            "app.js": "application/javascript; charset=utf-8",
            "video.js": "application/javascript; charset=utf-8",
            "style.css": "text/css; charset=utf-8",
            "video.css": "text/css; charset=utf-8",
        }
        path = WEB_DIR / name
        if not path.exists():
            self._error(404, "未找到")
            return
        self._send_bytes(
            path.read_bytes(),
            content_types.get(name, "application/octet-stream"),
            cache_control="private, max-age=300",
        )

    def _api_get(self, path: str) -> None:
        query = self._query()
        if path == "/api/summary":
            self._send_json(self._summary())
            return
        if path == "/api/video/summary":
            with self.state.lock:
                videos = list(self.state.videos)
                compressed = sum(
                    1 for video in videos if _video_history_entry(self.state, video)
                )
                payload = {
                    "count": len(videos),
                    "bytes": sum(video.size for video in videos),
                    "compressed_count": compressed,
                    "free_bytes": shutil.disk_usage(str(state_dir())).free,
                    "suggested_batch_bytes": self.state.video_batch_suggestion(),
                    "device": {
                        "model": self.state.model,
                        "android": self.state.android,
                    },
                    "scanned_at": self.state.scan.scanned_at,
                }
            self._send_json(payload)
            return
        if path == "/api/video/dates":
            with self.state.lock:
                self._send_json(
                    {
                        "dates": dates_for(self.state.videos),
                        "months": months_for(self.state.videos),
                        "albums": albums_for(self.state.videos),
                    }
                )
            return
        if path == "/api/video/list":
            date = (query.get("date") or [""])[0]
            month = (query.get("month") or [""])[0]
            album = (query.get("album") or [""])[0]
            with self.state.lock:
                if month:
                    if not re.fullmatch(r"\d{4}-\d{2}", month):
                        raise BadRequest("month 格式必须是 YYYY-MM")
                    videos = self.state.videos_for_month(month)
                elif album:
                    if album not in {row["album"] for row in self.state.video_albums()}:
                        raise BadRequest("未知视频相册")
                    videos = self.state.videos_for_album(album)
                elif date:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                        raise BadRequest("date 格式必须是 YYYY-MM-DD")
                    videos = self.state.videos_for_date(date)
                else:
                    videos = list(self.state.videos)
                payload = [_video_payload(video, self.state) for video in videos]
            self._send_json({"videos": payload})
            return
        if path == "/api/video/thumb":
            remote = (query.get("path") or [""])[0]
            video = self.state.video_by_path.get(remote)
            if video is None:
                raise BadRequest("不在视频清单中")
            thumb = _make_video_thumbnail(self.state, video)
            if thumb is None:
                self._error(404, "视频缩略图不可用")
                return
            self._send_file(thumb, "image/jpeg", "private, max-age=86400")
            return
        if path == "/api/dates":
            with self.state.lock:
                self._send_json(
                    {
                        "dates": self.state.dates(),
                        "months": self.state.months(),
                        "albums": self.state.albums(),
                    }
                )
            return
        if path == "/api/photos":
            date = (query.get("date") or [""])[0]
            month = (query.get("month") or [""])[0]
            album = (query.get("album") or [""])[0]
            if month:
                if not re.fullmatch(r"\d{4}-\d{2}", month):
                    raise BadRequest("month 格式必须是 YYYY-MM")
                with self.state.lock:
                    pairs = self.state.pairs_for_month(month)
                photos = [_photo_payload(pair, self.state) for pair in pairs]
                self._send_json({"month": month, "photos": photos})
                return
            if album:
                with self.state.lock:
                    known = {row["album"] for row in self.state.albums()}
                    if album not in known:
                        raise BadRequest("未知相册")
                    pairs = self.state.pairs_for_album(album)
                photos = [_photo_payload(pair, self.state) for pair in pairs]
                self._send_json({"album": album, "photos": photos})
                return
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise BadRequest("date 格式必须是 YYYY-MM-DD")
            with self.state.lock:
                pairs = self.state.pairs_for(date)
            self._send_json(
                {"date": date, "photos": [_photo_payload(pair, self.state) for pair in pairs]}
            )
            return
        if path == "/api/largest":
            try:
                limit = int((query.get("limit") or ["50"])[0])
            except ValueError as exc:
                raise BadRequest("limit 必须是整数") from exc
            limit = max(1, limit)
            with self.state.lock:
                pairs = sorted(self.state.pairs, key=lambda pair: -pair.size)[:limit]
            self._send_json(
                {"photos": [_photo_payload(pair, self.state) for pair in pairs]}
            )
            return
        if path == "/api/thumb":
            remote = (query.get("path") or [""])[0]
            pair = self.state.pairs_by_mp4.get(remote)
            if pair is None:
                raise BadRequest("不在实况照片配对清单中")
            thumb = _make_thumbnail(self.state, pair, self.state.ffmpeg)
            if thumb is None:
                self._error(404, "缩略图生成失败")
                return
            self._send_file(thumb, "image/jpeg", "private, max-age=86400")
            return
        if path.startswith("/api/jobs/") and path.endswith("/events"):
            job_id = path[len("/api/jobs/") : -len("/events")]
            self._stream_job(job_id)
            return
        if path.startswith("/api/jobs/"):
            job_id = path[len("/api/jobs/") :]
            job = self.state.jobs.get(job_id)
            if job is None:
                self._error(404, "任务不存在")
                return
            with self.state.lock:
                self._send_json(_job_payload(job))
            return
        if path == "/api/media":
            remote = (query.get("path") or [""])[0]
            self._serve_phone_video(remote)
            return
        if path == "/api/preview":
            job_id = (query.get("job") or [""])[0]
            remote = (query.get("path") or [""])[0]
            item = self.state.find_item(job_id, remote)
            if item is None:
                raise BadRequest("预览参数无效")
            out = _job_file(self.state, job_id, item.output)
            if out is None or not out.exists():
                self._error(404, "压缩结果不存在")
                return
            self._send_file(out, "video/mp4", "private, max-age=300")
            return
        if path == "/api/backups":
            with self.state.lock:
                sessions = _backup_sessions()
            self._send_json(
                {
                    "sessions": sessions,
                    "backup_dir": str(BACKUPS_DIR),
                    "total_bytes": sum(item["bytes"] for item in sessions),
                }
            )
            return
        self._error(404, "未知 API")

    def _summary(self) -> Dict:
        ffmpeg_ok = bool(
            self.state.ffmpeg
            and (shutil.which(self.state.ffmpeg) or os.path.exists(self.state.ffmpeg))
        )
        ffprobe_ok = bool(
            self.state.ffprobe
            and (shutil.which(self.state.ffprobe) or os.path.exists(self.state.ffprobe))
        )
        backup_bytes = 0
        seen_inodes = set()
        if BACKUPS_DIR.exists():
            for path in BACKUPS_DIR.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                key = (stat.st_dev, stat.st_ino)
                if key in seen_inodes:
                    continue
                seen_inodes.add(key)
                backup_bytes += stat.st_size
        try:
            local_free = shutil.disk_usage(str(state_dir())).free
        except OSError:
            local_free = 0
        with self.state.lock:
            active = self.state.active_job()
            active_id = active.id if active else None
        return {
            "device": {
                "model": self.state.model,
                "android": self.state.android,
                "serial": mask_serial(self.state.adb.serial),
            },
            "scanned_at": self.state.scan.scanned_at,
            "totals": {
                "pairs": len(self.state.pairs),
                "bytes": sum(pair.size for pair in self.state.pairs),
            },
            "dates": len(self.state.dates()),
            "ffmpeg": {
                "ok": ffmpeg_ok and ffprobe_ok,
                "path": self.state.ffmpeg,
                "ffprobe_path": self.state.ffprobe,
                "version": _tool_version(self.state.ffmpeg) if ffmpeg_ok else "",
                "ffprobe_version": _tool_version(self.state.ffprobe) if ffprobe_ok else "",
            },
            "hardware": {
                "ok": bool(self.state.hardware_encoder),
                "encoder": self.state.hardware_encoder,
            },
            "presets": _presets_payload(),
            "speeds": [{"key": key, "label": SPEED_LABELS[key]} for key in SPEED_ORDER],
            "default_speed": "hardware" if self.state.hardware_encoder else "veryfast",
            "workers": self.state.workers,
            "apply_workers": self.state.apply_workers,
            "history_count": len(self.state.history),
            "batch_suggestion": BATCH_SUGGESTION,
            "crf_range": [UI_CRF_MIN, UI_CRF_MAX],
            "backup_bytes": backup_bytes,
            "backup_dir": str(BACKUPS_DIR),
            "local_free": local_free,
            "active_job": active_id,
        }

    # -------------------------------------------------------------------- POST
    def do_POST(self) -> None:
        if not self._host_ok():
            self._error(403, "Host 校验失败")
            return
        if not self._origin_ok():
            self._error(403, "Origin 校验失败")
            return
        if not self._token_ok():
            self._error(403, "缺少或错误的访问 token")
            return
        parsed = urllib.parse.urlsplit(self.path)
        try:
            payload = _parse_json_body(self)
            self._api_post(parsed.path, payload)
        except BadRequest as exc:
            self._error(400, str(exc))
        except Busy as exc:
            self._error(409, str(exc))
        except (CompressError, ADBError) as exc:
            self._error(400, str(exc))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, f"{type(exc).__name__}: {exc}")

    def _api_post(self, path: str, payload: Dict) -> None:
        if path == "/api/compress":
            self._post_compress(payload)
            return
        if path == "/api/apply":
            self._post_apply(payload)
            return
        if path == "/api/video/compress":
            self._post_video_compress(payload)
            return
        if path == "/api/video/apply":
            self._post_video_apply(payload)
            return
        if path == "/api/video/restore":
            self._post_video_restore(payload)
            return
        if path == "/api/restore":
            self._post_restore(payload)
            return
        if path == "/api/restore-backups":
            self._post_restore_backups(payload)
            return
        if path == "/api/delete-backups":
            self._post_delete_backups(payload)
            return
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            self._post_cancel(path)
            return
        if path == "/api/scan":
            self._post_scan()
            return
        self._error(404, "未知 API")

    def _post_compress(self, payload: Dict) -> None:
        state = self.state
        if not (shutil.which(state.ffmpeg) or os.path.exists(state.ffmpeg)):
            raise BadRequest("找不到 ffmpeg，无法压缩。请先安装：brew install ffmpeg")
        if not (shutil.which(state.ffprobe) or os.path.exists(state.ffprobe)):
            raise BadRequest("找不到 ffprobe，无法压缩")
        paths = _string_list(payload)
        if not paths:
            raise BadRequest("没有选择照片")
        preset_key = str(payload.get("preset", "balanced"))
        crf_value = payload.get("crf")
        crf = None
        if crf_value is not None:
            try:
                crf = int(crf_value)
            except (TypeError, ValueError) as exc:
                raise BadRequest("crf 必须是整数") from exc
        preset = resolve_preset(preset_key, crf)
        if preset.mode == "encode" and preset.crf is not None and not (
            UI_CRF_MIN <= preset.crf <= UI_CRF_MAX
        ):
            raise BadRequest(f"界面 CRF 限制为 {UI_CRF_MIN}–{UI_CRF_MAX}")
        default_speed = "hardware" if state.hardware_encoder else "veryfast"
        speed = resolve_speed(str(payload.get("speed", default_speed)))
        if speed == "hardware" and not state.hardware_encoder:
            raise BadRequest("当前 ffmpeg 不支持 Apple 硬件编码，请选择快/均衡/最小体积")
        compute_ssim = bool(payload.get("compute_ssim", False))
        recompress = bool(payload.get("recompress", False))
        items = []
        total_bytes = 0
        for remote in paths:
            pair = state.pairs_by_mp4.get(remote)
            if pair is None:
                raise BadRequest(f"不在实况照片配对清单中: {remote}")
            item = ItemResult(remote, pair.jpg, pair.date, pair.time)
            skip_reason = _history_skip_reason(
                _history_entry(state, pair), recompress, preset
            )
            if skip_reason:
                item.status = "skipped"
                item.reason = skip_reason
                item.progress = 1.0
            else:
                total_bytes += pair.size
            items.append(item)
        if not any(item.status != "skipped" for item in items):
            raise BadRequest(
                "所选照片都已压缩过；勾选“允许更深压缩”，或选择小体积/更高 CRF 档位"
            )
        free = shutil.disk_usage(str(state_dir())).free
        needed = estimate_local_need(total_bytes)
        warning = ""
        if needed > free:
            warning = (
                f"本机空间不足：本批约需 {human(needed)}，当前可用 {human(free)}。"
                f"会先处理能容纳的文件，剩余标记为空间不足；建议每批约 {BATCH_SUGGESTION} 个。"
            )
        job = state.new_job(
            "compress",
            items,
            f"{preset.label} · {SPEED_LABELS[speed]} · {state.workers} 并发",
        )
        _start_thread(
            state,
            job,
            lambda: _compress_job(state, job, preset, speed, compute_ssim),
        )
        self._send_json(
            {"job_id": job.id, "warning": warning, "suggested_batch": BATCH_SUGGESTION},
            202,
        )

    def _post_apply(self, payload: Dict) -> None:
        state = self.state
        source_id = str(payload.get("job_id", ""))
        source_job = state.jobs.get(source_id)
        if source_job is None or source_job.kind != "compress":
            raise BadRequest("job_id 必须是压缩任务")
        if source_job.status == "running":
            raise Busy("压缩任务还在运行")
        requested = _string_list(payload)
        ready = [item for item in source_job.items if item.status == "ready"]
        if requested:
            ready_map = {item.mp4: item for item in ready}
            chosen = []
            for remote in requested:
                item = ready_map.get(remote)
                if item is None:
                    raise BadRequest(f"该文件没有可用的压缩结果: {remote}")
                chosen.append(item)
        else:
            chosen = ready
        if not chosen:
            raise BadRequest("没有可应用的结果")
        items = [
            ItemResult(item.mp4, item.jpg, item.date, item.time)
            for item in chosen
        ]
        job = state.new_job("apply", items)
        _start_thread(
            state,
            job,
            lambda: _apply_job(state, job, source_job),
        )
        self._send_json({"job_id": job.id}, 202)

    def _post_video_compress(self, payload: Dict) -> None:
        state = self.state
        if not (shutil.which(state.ffmpeg) or os.path.exists(state.ffmpeg)):
            raise BadRequest("找不到 ffmpeg，无法压缩")
        if not (shutil.which(state.ffprobe) or os.path.exists(state.ffprobe)):
            raise BadRequest("找不到 ffprobe，无法压缩")
        paths = _string_list(payload)
        if not paths:
            raise BadRequest("没有选择视频")
        preset = VIDEO_PRESETS.get(str(payload.get("preset", "balanced")))
        if preset is None:
            raise BadRequest("未知的视频压缩档位")
        items = []
        total_bytes = 0
        for remote in paths:
            video = state.video_by_path.get(remote)
            if video is None:
                raise BadRequest(f"不在视频清单中: {remote}")
            item = ItemResult(video.path, "", video.date, video.time)
            entry = _video_history_entry(state, video)
            if entry:
                item.status = "skipped"
                item.progress = 1.0
                crf = entry.get("crf_used")
                label = f"CRF{crf}" if crf is not None else "档位未知"
                item.reason = f"已压缩过（{label}）；视频赛道暂不支持重复压缩"
            else:
                total_bytes += video.size
            items.append(item)
        if not any(item.status != "skipped" for item in items):
            raise BadRequest("所选视频都已压缩过")
        free = shutil.disk_usage(str(state_dir())).free
        needed = estimate_local_need(total_bytes)
        warning = ""
        if needed > free:
            warning = (
                f"本机空间不足：本批约需 {human(needed)}，当前可用 {human(free)}。"
                "会先处理能容纳的视频，其余标记为空间不足；建议按总大小分批。"
            )
        job = state.new_job(
            "video-compress",
            items,
            f"{preset.label} · 视频 · {VIDEO_WORKERS} 并发",
        )
        _start_thread(
            state,
            job,
            lambda: _video_compress_job(state, job, preset),
        )
        self._send_json(
            {
                "job_id": job.id,
                "warning": warning,
                "suggested_batch_bytes": state.video_batch_suggestion(),
            },
            202,
        )

    def _post_video_apply(self, payload: Dict) -> None:
        state = self.state
        source_id = str(payload.get("job_id", ""))
        source_job = state.jobs.get(source_id)
        if source_job is None or source_job.kind != "video-compress":
            raise BadRequest("job_id 必须是视频压缩任务")
        if source_job.status == "running":
            raise Busy("压缩任务还在运行")
        requested = _string_list(payload)
        ready = [item for item in source_job.items if item.status == "ready"]
        if requested:
            ready_map = {item.mp4: item for item in ready}
            chosen = []
            for remote in requested:
                item = ready_map.get(remote)
                if item is None:
                    raise BadRequest(f"该视频没有可用的压缩结果: {remote}")
                chosen.append(item)
        else:
            chosen = ready
        if not chosen:
            raise BadRequest("没有可应用的视频")
        items = [
            ItemResult(item.mp4, "", item.date, item.time) for item in chosen
        ]
        job = state.new_job("video-apply", items)
        _start_thread(
            state,
            job,
            lambda: _video_apply_job(state, job, source_job),
        )
        self._send_json({"job_id": job.id}, 202)

    def _post_video_restore(self, payload: Dict) -> None:
        state = self.state
        source_id = str(payload.get("job_id", ""))
        source_job = state.jobs.get(source_id)
        if source_job is None or source_job.kind != "video-compress":
            raise BadRequest("job_id 必须是视频压缩任务")
        requested = _string_list(payload)
        applied = [item for item in source_job.items if item.status == "applied"]
        if requested:
            applied_map = {item.mp4: item for item in applied}
            chosen = []
            for remote in requested:
                item = applied_map.get(remote)
                if item is None:
                    raise BadRequest(f"该视频没有可恢复的备份: {remote}")
                chosen.append(item)
        else:
            chosen = applied
        if not chosen:
            raise BadRequest("没有可恢复的视频")
        items = [
            ItemResult(item.mp4, "", item.date, item.time) for item in chosen
        ]
        job = state.new_job("video-restore", items)
        _start_thread(
            state,
            job,
            lambda: _video_restore_job(state, job, source_job),
        )
        self._send_json({"job_id": job.id}, 202)

    def _post_restore(self, payload: Dict) -> None:
        state = self.state
        source_id = str(payload.get("job_id", ""))
        source_job = state.jobs.get(source_id)
        if source_job is None or source_job.kind != "compress":
            raise BadRequest("job_id 必须是压缩任务")
        requested = _string_list(payload)
        applied = [item for item in source_job.items if item.status == "applied"]
        if requested:
            applied_map = {item.mp4: item for item in applied}
            chosen = []
            for remote in requested:
                item = applied_map.get(remote)
                if item is None:
                    raise BadRequest(f"该文件没有可恢复的备份: {remote}")
                chosen.append(item)
        else:
            chosen = applied
        if not chosen:
            raise BadRequest("没有可恢复的文件")
        items = [ItemResult(item.mp4, item.jpg, item.date, item.time) for item in chosen]
        job = state.new_job("restore", items)
        _start_thread(
            state,
            job,
            lambda: _restore_job(state, job, source_job),
        )
        self._send_json({"job_id": job.id}, 202)

    def _post_cancel(self, path: str) -> None:
        job_id = path[len("/api/jobs/") : -len("/cancel")]
        job = self.state.jobs.get(job_id)
        if job is None:
            self._error(404, "任务不存在")
            return
        if job.status != "running":
            raise BadRequest("任务已经结束")
        if job.kind not in ("compress", "video-compress"):
            raise BadRequest("只有压缩任务支持中途停止")
        job.cancel_requested = True
        job.message = "正在停止，等待当前文件结束…"
        self._send_json({"cancelled": True, "job_id": job.id})

    def _post_delete_backups(self, payload: Dict) -> None:
        state = self.state
        if payload.get("confirm") != "delete-backups":
            raise BadRequest("删除备份需要显式确认")
        if state.active_job() is not None:
            raise Busy("有任务在运行，完成后再删除备份")
        session_name = str(payload.get("session", ""))
        if session_name == "*":
            sessions = _backup_sessions()
            deleted_files = sum(item["count"] for item in sessions)
            for item in sessions:
                _delete_backup_session(item["session"])
            self._send_json(
                {"deleted_sessions": len(sessions), "deleted_files": deleted_files}
            )
            return
        count = _delete_backup_session(session_name)
        self._send_json({"deleted_sessions": 1, "deleted_files": count})

    def _post_restore_backups(self, payload: Dict) -> None:
        state = self.state
        session_name = str(payload.get("session", ""))
        sessions = _backup_sessions()
        session = next((item for item in sessions if item["session"] == session_name), None)
        if session is None:
            raise BadRequest("备份批次不存在")
        entries = {entry["mp4"]: entry for entry in session["entries"]}
        requested = _string_list(payload)
        if requested:
            chosen = []
            for remote in requested:
                entry = entries.get(remote)
                if entry is None:
                    raise BadRequest(f"该文件不在备份批次中: {remote}")
                chosen.append(entry)
        else:
            chosen = session["entries"]
        if not chosen:
            raise BadRequest("该批次没有可恢复的文件")
        items = [
            ItemResult(entry["mp4"], entry["jpg"], entry["date"], entry["time"])
            for entry in chosen
        ]
        job = state.new_job("restore-backup", items)
        _start_thread(
            state,
            job,
            lambda: _restore_backup_job(state, job, session_name),
        )
        self._send_json({"job_id": job.id}, 202)

    def _post_scan(self) -> None:
        state = self.state
        job = state.new_job("scan", [])
        job.total = 1
        _start_thread(
            state,
            job,
            lambda: _scan_job(state, job),
        )
        self._send_json({"job_id": job.id}, 202)


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, state: StudioState):
        self.state = state
        super().__init__(address, handler)


def _load_pairs(adb: ADB, scan: Scan, max_duration: int, include_unknown: bool):
    durations = load_durations(adb)
    pairs, too_long, unknown = find_pairs(
        scan.entries,
        durations,
        max_duration_ms=max_duration,
        include_unknown=include_unknown,
    )
    return durations, pairs, too_long, unknown


@dataclass
class StudioRuntime:
    server: "StudioServer"
    state: StudioState
    url: str
    pairs: List
    unknown: int
    too_long: int


def prepare_studio(
    args, status: Optional[Callable[[str], None]] = None
) -> StudioRuntime:
    """Create the local server without blocking; serve() and the Mac app share this."""
    def notify(message: str) -> None:
        if status is not None:
            status(message)

    _migrate_legacy_backups()
    host = getattr(args, "host", "127.0.0.1")
    if host not in ALLOWED_HOSTS:
        print(f"[!] 只允许本机访问，已忽略 --host {host}，改用 127.0.0.1")
        host = "127.0.0.1"
    adb = ADB.connect(getattr(args, "adb", None), getattr(args, "serial", None))
    scan = load_latest(adb.serial) if not getattr(args, "rescan", False) else None
    if getattr(args, "cached", False) and scan is None:
        raise ADBError("没有可用的扫描缓存。请先运行 klean scan，或去掉 --cached")
    if scan is None:
        print("[i] 正在扫描 /sdcard（通常 30–120 秒，请保持解锁）...")
        notify("首次扫描手机存储，可能需要 1–2 分钟…")
        scan = scan_device(adb, "/sdcard")
        save_scan(scan)
    else:
        print(f"[i] 使用扫描缓存：{scan.scanned_at}（{len(scan.files):,} 个文件）")
    print("[i] 读取 MediaStore 视频时长...")
    notify("正在读取媒体信息…")
    durations, pairs, too_long, unknown = _load_pairs(
        adb, scan, getattr(args, "max_duration", 5000), getattr(args, "include_unknown", False)
    )
    if not durations:
        print("[!] MediaStore 时长不可用；界面只显示命名匹配且未校验时长的文件。")
    notify("正在读取视频列表…")
    try:
        videos = load_videos(
            adb,
            live_paths={pair.mp4 for pair in pairs},
            size_map=scan.size_map(),
        )
    except (ADBError, OSError) as exc:
        print(f"[!] 读取视频列表失败：{exc}")
        videos = []
    props = adb.props()
    model = props.get("ro.product.model", "") or props.get("ro.product.vendor.model", "")
    android = props.get("ro.build.version.release", "")
    hardware_encoder = detect_hardware_encoder(getattr(args, "ffmpeg", "ffmpeg"))
    requested_workers = int(getattr(args, "workers", 0) or 0)
    if requested_workers > 0:
        workers = max(1, min(requested_workers, 8))
    elif hardware_encoder and (os.cpu_count() or 0) >= 12:
        # Base M-series media engines keep scaling until ~6 in-flight jobs;
        # more only adds contention. 12+ CPU cores (M6 class) gets 6.
        workers = 6
    else:
        workers = 3
    token = secrets.token_urlsafe(32)
    port = int(getattr(args, "port", 8765))
    state = StudioState(
        adb=adb,
        scan=scan,
        pairs=pairs,
        pairs_by_mp4={pair.mp4: pair for pair in pairs},
        durations=durations,
        token=token,
        port=port,
        ffmpeg=getattr(args, "ffmpeg", "ffmpeg"),
        ffprobe=getattr(args, "ffprobe", "ffprobe"),
        max_duration=getattr(args, "max_duration", 5000),
        include_unknown=getattr(args, "include_unknown", False),
        workers=workers,
        apply_workers=max(1, min(int(getattr(args, "apply_workers", 4)), 4)),
        hardware_encoder=hardware_encoder,
        history=_load_history(),
        videos=videos,
        video_by_path={video.path: video for video in videos},
        video_by_id={video.media_id: video for video in videos},
        video_history=_load_video_history(),
        model=model,
        android=android,
    )
    server = StudioServer((host, port), StudioHandler, state)
    bound_port = server.server_port
    state.port = bound_port
    url = f"http://127.0.0.1:{bound_port}/?token={token}"
    notify("工作台已准备好")
    return StudioRuntime(
        server=server,
        state=state,
        url=url,
        pairs=pairs,
        unknown=len(unknown),
        too_long=len(too_long),
    )


def serve(args) -> int:
    runtime = prepare_studio(args)
    server = runtime.server
    state = runtime.state
    url = runtime.url
    print()
    print(f"  Klean Studio  {state.model}  Android {state.android}")
    print(f"  实况照片 {len(runtime.pairs):,} 个 / {human(sum(pair.size for pair in runtime.pairs))}")
    print(f"  普通视频 {len(state.videos):,} 个 / {human(sum(video.size for video in state.videos))}")
    print(
        f"  日期 {len(state.dates()):,} 天；时长未知 {runtime.unknown:,} 个；"
        f"超长 {runtime.too_long:,} 个"
    )
    print(
        f"  并行压缩 {state.workers} 路 / 应用 {state.apply_workers} 路；默认 "
        + ("hardware（VideoToolbox）" if state.hardware_encoder else "veryfast")
    )
    print(f"  工作台 {url}")
    print(f"  任务目录 {JOBS_DIR}")
    print(f"  备份目录 {BACKUPS_DIR}")
    print("  按 Ctrl+C 停止。JPG 永远不会被修改。")
    print()
    if not getattr(args, "no_browser", False):
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[i] 正在停止...")
    finally:
        server.server_close()
    return 0
