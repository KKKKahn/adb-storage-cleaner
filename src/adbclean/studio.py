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
from .util import data_dir, human, mask_serial, now_iso, stamp, state_dir

WEB_DIR = Path(__file__).resolve().with_name("web")
STUDIO_DIR = state_dir() / "studio"
JOBS_DIR = STUDIO_DIR / "jobs"
THUMBS_DIR = STUDIO_DIR / "thumbs"
BACKUPS_DIR = data_dir() / "studio" / "backups"
LEGACY_BACKUPS_DIR = STUDIO_DIR / "backups"
MAX_BODY = 1024 * 1024
MAX_JOB_ITEMS = 500
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
        "# Generated by adbclean studio: restore backed-up live-photo MP4s.",
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
    temp = remote.rsplit("/", 1)[0] + "/." + remote.rsplit("/", 1)[-1] + ".adbclean.tmp"
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
            job.done += 1
        return
    with state.lock:
        item.status = "running"
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
        result = compress_file(
            str(local_src),
            str(local_out),
            preset,
            ffmpeg=state.ffmpeg,
            ffprobe=state.ffprobe,
            compute_ssim=compute_ssim,
            speed=speed,
        )
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
            item.status = "ready"
            item.reason = "音频已重编码" if result.audio_reencoded else ""
    except (HdrUnsupported, UnsupportedFormat, NoVivoUuid, NoBenefit) as exc:
        with state.lock:
            item.status = "skipped"
            item.reason = str(exc)
    except (BadRequest, CompressError, ADBError, OSError, ValueError) as exc:
        with state.lock:
            item.status = "error"
            item.reason = str(exc)
    except Exception as exc:  # keep one bad file from aborting a batch
        with state.lock:
            item.status = "error"
            item.reason = f"{type(exc).__name__}: {exc}"
    finally:
        with state.lock:
            item.elapsed_ms = int((time.monotonic() - started) * 1000)
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
        source_local = _job_file(state, source_job.id, src.source)
        if source_local is None or not source_local.exists():
            raise CompressError("本地原始备份副本丢失，已跳过")
        target = _backup_target(backup_dir, pair.mp4)
        _backup_verified(source_local, target, src.original_md5)
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
            item.elapsed_ms = int((time.monotonic() - started) * 1000)
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
        job.message = f"已应用 {len(manifest_rows)} 个；备份目录 {backup_dir}"
    else:
        with contextlib.suppress(OSError):
            backup_dir.rmdir()

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
    }


def _job_payload(job: Job) -> Dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "total": job.total,
        "done": job.done,
        "message": job.message,
        "error": job.error,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "cancel_requested": job.cancel_requested,
        "items": [_item_payload(item) for item in job.items],
    }


def _photo_payload(pair: LivePair) -> Dict:
    return {
        "mp4": pair.mp4,
        "jpg": pair.jpg,
        "date": pair.date,
        "time": pair.time,
        "size": pair.size,
        "duration_ms": pair.duration_ms,
        "unknown_duration": pair.duration_ms is None,
        "album": _album_of(pair.mp4),
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
    if len(value) > MAX_JOB_ITEMS:
        raise BadRequest(f"一次最多处理 {MAX_JOB_ITEMS} 个文件")
    seen = set()
    result = []
    for item in value:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


class StudioHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "adbclean-studio"

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
        token = self.headers.get("X-Adbclean-Token", "")
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
        if path in ("/", "/index.html"):
            if not self._token_ok():
                self._error(403, "缺少或错误的访问 token")
                return
            self._serve_index()
            return
        if path in ("/static/app.js", "/static/style.css"):
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
        index = WEB_DIR / "index.html"
        if not index.exists():
            self._error(500, "前端资源缺失")
            return
        text = index.read_text(encoding="utf-8")
        text = text.replace("__ADBCLEAN_TOKEN__", html.escape(self.state.token, quote=True))
        data = text.encode("utf-8")
        self._send_bytes(data, "text/html; charset=utf-8")

    def _serve_static(self, name: str) -> None:
        content_types = {
            "app.js": "application/javascript; charset=utf-8",
            "style.css": "text/css; charset=utf-8",
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
                photos = [_photo_payload(pair) for pair in pairs]
                self._send_json({"month": month, "photos": photos})
                return
            if album:
                with self.state.lock:
                    known = {row["album"] for row in self.state.albums()}
                    if album not in known:
                        raise BadRequest("未知相册")
                    pairs = self.state.pairs_for_album(album)
                photos = [_photo_payload(pair) for pair in pairs]
                self._send_json({"album": album, "photos": photos})
                return
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise BadRequest("date 格式必须是 YYYY-MM-DD")
            with self.state.lock:
                pairs = self.state.pairs_for(date)
            self._send_json({"date": date, "photos": [_photo_payload(pair) for pair in pairs]})
            return
        if path == "/api/largest":
            try:
                limit = int((query.get("limit") or ["50"])[0])
            except ValueError as exc:
                raise BadRequest("limit 必须是整数") from exc
            limit = max(1, min(limit, MAX_JOB_ITEMS))
            with self.state.lock:
                pairs = sorted(self.state.pairs, key=lambda pair: -pair.size)[:limit]
            self._send_json({"photos": [_photo_payload(pair) for pair in pairs]})
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
        if path.startswith("/api/jobs/"):
            job_id = path[len("/api/jobs/") :]
            job = self.state.jobs.get(job_id)
            if job is None:
                self._error(404, "任务不存在")
                return
            with self.state.lock:
                self._send_json(_job_payload(job))
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
        items = []
        total_bytes = 0
        for remote in paths:
            pair = state.pairs_by_mp4.get(remote)
            if pair is None:
                raise BadRequest(f"不在实况照片配对清单中: {remote}")
            total_bytes += pair.size
            items.append(ItemResult(remote, pair.jpg, pair.date, pair.time))
        free = shutil.disk_usage(str(state_dir())).free
        needed = estimate_local_need(total_bytes)
        if needed > free:
            raise BadRequest(
                f"本机空间不足：本批约需 {human(needed)}，当前可用 {human(free)}。"
                "请减少选择数量，或先清理本机任务缓存。"
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
        self._send_json({"job_id": job.id}, 202)

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
        if job.kind != "compress":
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


def serve(args) -> int:
    _migrate_legacy_backups()
    host = getattr(args, "host", "127.0.0.1")
    if host not in ALLOWED_HOSTS:
        print(f"[!] 只允许本机访问，已忽略 --host {host}，改用 127.0.0.1")
        host = "127.0.0.1"
    adb = ADB.connect(getattr(args, "adb", None), getattr(args, "serial", None))
    scan = load_latest(adb.serial) if not getattr(args, "rescan", False) else None
    if getattr(args, "cached", False) and scan is None:
        raise ADBError("没有可用的扫描缓存。请先运行 adbclean scan，或去掉 --cached")
    if scan is None:
        print("[i] 正在扫描 /sdcard（通常 30–120 秒，请保持解锁）...")
        scan = scan_device(adb, "/sdcard")
        save_scan(scan)
    else:
        print(f"[i] 使用扫描缓存：{scan.scanned_at}（{len(scan.files):,} 个文件）")
    print("[i] 读取 MediaStore 视频时长...")
    durations, pairs, too_long, unknown = _load_pairs(
        adb, scan, getattr(args, "max_duration", 5000), getattr(args, "include_unknown", False)
    )
    if not durations:
        print("[!] MediaStore 时长不可用；界面只显示命名匹配且未校验时长的文件。")
    props = adb.props()
    model = props.get("ro.product.model", "") or props.get("ro.product.vendor.model", "")
    android = props.get("ro.build.version.release", "")
    hardware_encoder = detect_hardware_encoder(getattr(args, "ffmpeg", "ffmpeg"))
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
        workers=max(1, min(int(getattr(args, "workers", 3)), 8)),
        apply_workers=max(1, min(int(getattr(args, "apply_workers", 4)), 4)),
        hardware_encoder=hardware_encoder,
        model=model,
        android=android,
    )
    server = StudioServer((host, port), StudioHandler, state)
    url = f"http://127.0.0.1:{port}/?token={token}"
    print()
    print(f"  adbclean Studio  {model}  Android {android}")
    print(f"  实况照片 {len(pairs):,} 个 / {human(sum(pair.size for pair in pairs))}")
    print(f"  日期 {len(state.dates()):,} 天；时长未知 {len(unknown):,} 个；超长 {len(too_long):,} 个")
    print(
        f"  并行压缩 {state.workers} 路 / 应用 {state.apply_workers} 路；默认 "
        + ("hardware（VideoToolbox）" if hardware_encoder else "veryfast")
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
