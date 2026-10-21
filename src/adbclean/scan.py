"""Scan /sdcard over ADB and cache the inventory locally."""

from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .adb import ADB, ADBError, quote
from .classify import is_secondary_user_path, normalize
from .util import now_iso, stamp, state_dir

SCAN_VERSION = 1


@dataclass
class Entry:
    path: str
    size: int
    is_dir: bool = False


@dataclass
class Scan:
    serial: str
    root: str
    scanned_at: str
    entries: List[Entry] = field(default_factory=list)
    raw_lines: int = 0

    @property
    def files(self) -> List[Entry]:
        return [entry for entry in self.entries if not entry.is_dir]

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.files)

    @property
    def total_files(self) -> int:
        return len(self.files)

    def size_map(self) -> Dict[str, int]:
        return {entry.path: entry.size for entry in self.entries}


def _looks_like_dir(paths: List[str], index: int) -> bool:
    if index + 1 >= len(paths):
        return False
    return paths[index + 1].startswith(paths[index] + "/")


def parse_du(text: str, root: str = "/sdcard", raw_lines: int = 0) -> Scan:
    """Parse `du -ab` output (size<TAB>path, one entry per line)."""
    root = root.rstrip("/") or "/"
    prefix = root + "/"
    records: Dict[str, int] = {}
    total_lines = raw_lines
    for line in text.splitlines():
        total_lines += 1
        line = line.rstrip("\r")
        if not line:
            continue
        if "\t" in line:
            size_text, path = line.split("\t", 1)
        else:
            match = re.match(r"^(\d+)\s+(/.*)$", line)
            if not match:
                continue
            size_text, path = match.group(1), match.group(2)
        if not size_text.isdigit():
            continue
        path = path.strip()
        if path != root and not path.startswith(prefix):
            continue
        path = path.rstrip("/") or "/"
        if "\x00" in path:
            continue
        records[path] = int(size_text)

    records.setdefault(root, 0)
    paths = sorted(records)
    dirs = {path for index, path in enumerate(paths) if _looks_like_dir(paths, index)}
    dirs.add(root)
    if records[root] == 0:
        # `du` normally prints the root itself; if it did not, sum file sizes
        # only (summing every record would double-count directories).
        records[root] = sum(size for path, size in records.items() if path not in dirs)

    entries = [Entry(path=path, size=records[path], is_dir=path in dirs) for path in paths]
    return Scan(serial="", root=root, scanned_at=now_iso(), entries=entries, raw_lines=total_lines)


def scan_device(adb: ADB, root: str = "/sdcard", timeout: int = 900) -> Scan:
    root = normalize(root)
    if not (root == "/sdcard" or root.startswith("/sdcard/") or root == "/storage/emulated/0"):
        raise ADBError("只支持扫描 /sdcard 或 /storage/emulated/0（当前用户）。")
    if is_secondary_user_path(root):
        raise ADBError("拒绝扫描隐私空间/应用分身，请只扫描当前用户（User 0）。")
    target = root.rstrip("/") + "/"
    text = adb.sh(f"du -ab {quote(target)}", timeout=timeout, check=False)
    if not text.strip():
        raise ADBError(
            "du 没有返回任何数据。请确认屏幕已解锁、USB 调试已授权，"
            "并尝试重新插拔数据线。"
        )
    scan = parse_du(text, root=root, raw_lines=0)
    scan.serial = adb.serial or "unknown"
    if not scan.entries:
        raise ADBError("扫描结果为空，可能被系统限制。请解锁手机后重试。")
    return scan


def _safe_serial(serial: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", serial or "unknown")


def cache_path(serial: str) -> Path:
    return state_dir() / f"scan-{_safe_serial(serial)}.json.gz"


def save_scan(scan: Scan) -> str:
    path = cache_path(scan.serial)
    payload = {
        "version": SCAN_VERSION,
        "serial": scan.serial,
        "root": scan.root,
        "scanned_at": scan.scanned_at,
        "raw_lines": scan.raw_lines,
        "entries": [[entry.path, entry.size, 1 if entry.is_dir else 0] for entry in scan.entries],
    }
    tmp = path.with_suffix(path.suffix + f".{stamp()}.tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", errors="surrogateescape") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return str(path)


def load_scan(path: Path) -> Scan:
    with gzip.open(path, "rt", encoding="utf-8", errors="surrogateescape") as handle:
        payload = json.load(handle)
    entries = [
        Entry(path=item[0], size=int(item[1]), is_dir=bool(item[2]))
        for item in payload.get("entries", [])
    ]
    return Scan(
        serial=payload.get("serial", "unknown"),
        root=payload.get("root", "/sdcard"),
        scanned_at=payload.get("scanned_at", ""),
        entries=entries,
        raw_lines=int(payload.get("raw_lines", 0)),
    )


def load_latest(serial: str) -> Optional[Scan]:
    path = cache_path(serial)
    if not path.exists():
        return None
    try:
        return load_scan(path)
    except (OSError, ValueError, KeyError):
        return None


def iter_children(entries: Iterable[Entry], directory: str) -> Iterable[Entry]:
    prefix = directory.rstrip("/") + "/"
    for entry in entries:
        if entry.path.startswith(prefix):
            yield entry
