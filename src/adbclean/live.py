"""Detect live-photo companions (JPG + MP4) and optionally remove the MP4s.

Live photos are stored as a still image plus a short MP4. Many Android skins
exclude the companion MP4 from both the "Photos" and "Videos" categories, so
the space shows up under "Other files". This module reports that space and can
delete the companions while keeping every JPG — explicit opt-in only.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .adb import ADB
from .classify import is_hidden_path
from .util import human, write_manifest

LIVE_RE = re.compile(r"^IMG_(\d{8})_(\d{6})\.mp4$", re.IGNORECASE)
JPG_EXT = (".jpg", ".jpeg")


@dataclass
class LivePair:
    mp4: str
    jpg: str
    size: int
    duration_ms: Optional[int]
    date: str
    time: str
    hidden: bool = False

    @property
    def month(self) -> str:
        return self.date[:7]


def load_durations(adb: ADB) -> Dict[str, int]:
    """Read durations from MediaStore (fast, no ffprobe needed)."""
    text = adb.content_query(
        "content://media/external/video/media",
        "_data:duration",
        timeout=240,
    )
    durations: Dict[str, int] = {}
    for line in text.splitlines():
        match = re.search(r"_data=(.*?), duration=(\d+)", line)
        if not match:
            continue
        path = match.group(1)
        for prefix in ("/storage/emulated/0", "/storage/self/primary"):
            if path.startswith(prefix):
                path = "/sdcard" + path[len(prefix) :]
                break
        if not path.startswith("/sdcard/"):
            continue
        try:
            durations[path] = int(match.group(2))
        except ValueError:
            continue
    return durations


def find_pairs(
    entries: Iterable,
    durations: Optional[Dict[str, int]] = None,
    max_duration_ms: int = 5000,
    include_unknown: bool = False,
) -> Tuple[List[LivePair], List[LivePair], List[LivePair]]:
    """Return (pairs, too_long, unknown_duration)."""
    files = [entry for entry in entries if not getattr(entry, "is_dir", False)]
    jpg_by_stem: Dict[str, str] = {}
    for entry in files:
        stem, ext = os.path.splitext(entry.path)
        if ext.lower() in JPG_EXT:
            jpg_by_stem[stem.lower()] = entry.path

    pairs: List[LivePair] = []
    too_long: List[LivePair] = []
    unknown: List[LivePair] = []
    for entry in files:
        stem, ext = os.path.splitext(entry.path)
        if ext.lower() != ".mp4":
            continue
        match = LIVE_RE.match(os.path.basename(entry.path))
        if not match:
            continue
        jpg = jpg_by_stem.get(stem.lower())
        if not jpg:
            continue
        day, clock = match.group(1), match.group(2)
        pair = LivePair(
            mp4=entry.path,
            jpg=jpg,
            size=entry.size,
            duration_ms=None,
            date=f"{day[:4]}-{day[4:6]}-{day[6:8]}",
            time=f"{clock[:2]}:{clock[2:4]}:{clock[4:6]}",
            hidden=is_hidden_path(entry.path),
        )
        if durations is None:
            pairs.append(pair)
            continue
        duration = durations.get(entry.path)
        if duration is None:
            pair.duration_ms = None
            unknown.append(pair)
            if include_unknown:
                pairs.append(pair)
            continue
        pair.duration_ms = duration
        if duration > max_duration_ms:
            too_long.append(pair)
        else:
            pairs.append(pair)
    pairs.sort(key=lambda item: item.mp4)
    too_long.sort(key=lambda item: item.mp4)
    unknown.sort(key=lambda item: item.mp4)
    return pairs, too_long, unknown


def month_stats(pairs: Sequence[LivePair]) -> List[Tuple[str, int, int]]:
    totals: Dict[str, List[int]] = {}
    for pair in pairs:
        bucket = totals.setdefault(pair.month, [0, 0])
        bucket[0] += 1
        bucket[1] += pair.size
    return sorted(((month, values[0], values[1]) for month, values in totals.items()), reverse=True)


def total_size(pairs: Sequence[LivePair]) -> int:
    return sum(pair.size for pair in pairs)


def _relpath(path: str) -> str:
    return path[len("/sdcard/") :] if path.startswith("/sdcard/") else path.lstrip("/")


def backup_pairs(
    adb: ADB,
    pairs: Sequence[LivePair],
    backup_dir: str,
    progress=None,
) -> Tuple[List[LivePair], List[Tuple[LivePair, str]], str]:
    """Copy MP4s to the computer before deletion; verify size afterwards."""
    os.makedirs(backup_dir, exist_ok=True)
    backed_up: List[LivePair] = []
    failures: List[Tuple[LivePair, str]] = []
    for index, pair in enumerate(pairs, start=1):
        local = os.path.join(backup_dir, _relpath(pair.mp4))
        ok = adb.pull(pair.mp4, local)
        if not ok:
            failures.append((pair, "adb pull 失败"))
        else:
            try:
                local_size = os.path.getsize(local)
            except OSError:
                local_size = -1
            if local_size != pair.size:
                failures.append((pair, f"备份大小不一致 {local_size} != {pair.size}"))
            else:
                backed_up.append(pair)
        if progress and index % 100 == 0:
            progress(index, len(pairs))
    restore_script = _write_restore_script(backup_dir, backed_up)
    return backed_up, failures, restore_script


def _write_restore_script(backup_dir: str, pairs: Sequence[LivePair]) -> str:
    path = os.path.join(backup_dir, "restore.sh")
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by adbclean: restore live-photo MP4 companions.",
        "set -euo pipefail",
        'ADB="${ADB:-adb}"',
        "",
    ]
    for pair in pairs:
        local = os.path.join(backup_dir, _relpath(pair.mp4))
        lines.append(f'"$ADB" push {sh_quote(local)} {sh_quote(pair.mp4)}')
        lines.append(
            '"$ADB" shell am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE '
            f"-d {sh_quote('file://' + pair.mp4)} >/dev/null"
        )
    lines.append("")
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as handle:
        handle.write("\n".join(lines))
    os.chmod(path, 0o755)
    return path


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def delete_pairs(
    adb: ADB,
    pairs: Sequence[LivePair],
    batch_size: int = 20,
    record_manifest: bool = True,
) -> Tuple[int, int, List[str], List[Tuple[LivePair, str]], Optional[str]]:
    """Delete MP4s but never a JPG. Returns (count, bytes, failures, skipped, manifest)."""
    deleted = 0
    freed = 0
    failures: List[str] = []
    skipped: List[Tuple[LivePair, str]] = []
    manifest_rows: List[List[str]] = []
    batches = [list(pairs[index : index + batch_size]) for index in range(0, len(pairs), batch_size)]
    for batch in batches:
        jpg_list = " ".join(sh_quote(pair.jpg) for pair in batch)
        missing_text = adb.sh(
            f'for f in {jpg_list}; do [ -f "$f" ] || echo "$f"; done',
            check=False,
        )
        missing = {line.strip() for line in missing_text.splitlines() if line.strip()}
        usable: List[LivePair] = []
        for pair in batch:
            if pair.jpg in missing:
                skipped.append((pair, "同名 JPG 不存在，已跳过"))
            else:
                usable.append(pair)
        if not usable:
            continue
        remote_list = " ".join(sh_quote(pair.mp4) for pair in usable)
        output = adb.sh(
            f"rm -f -- {remote_list}; "
            f'for f in {remote_list}; do [ -e "$f" ] && echo "$f"; done',
            check=False,
        )
        still_there = {line.strip() for line in output.splitlines() if line.strip()}
        for pair in usable:
            if pair.mp4 in still_there:
                failures.append(pair.mp4)
                continue
            deleted += 1
            freed += pair.size
            manifest_rows.append(
                [
                    pair.mp4,
                    pair.jpg,
                    str(pair.size),
                    str(pair.duration_ms or ""),
                    pair.date,
                    pair.time,
                ]
            )
    manifest = None
    if record_manifest and manifest_rows:
        manifest = write_manifest(
            "live-photo-mp4",
            ["deleted_mp4", "kept_jpg", "bytes", "duration_ms", "date", "time"],
            manifest_rows,
        )
    return deleted, freed, failures, skipped, manifest


def summary_line(pairs: Sequence[LivePair]) -> str:
    return f"{len(pairs):,} 个 / {human(total_size(pairs))}"
