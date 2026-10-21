"""Whitelisted cache/thumbnail cleanup (dry-run by default)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .adb import ADB
from .classify import assert_cleanable, junk_dirs
from .util import SafetyError, write_manifest


@dataclass
class JunkItem:
    path: str
    size: int
    files: int


def plan(scan) -> List[JunkItem]:
    items = [JunkItem(path=row[0], size=row[1], files=row[2]) for row in junk_dirs(scan.entries)]
    items.sort(key=lambda item: -item.size)
    return items


def total_size(items: Sequence[JunkItem]) -> int:
    return sum(item.size for item in items)


def apply(
    adb: ADB,
    items: Sequence[JunkItem],
    batch_size: int = 20,
    record_manifest: bool = True,
) -> Tuple[int, int, List[str], List[str], Optional[str]]:
    """Delete whitelisted directories. Returns (dirs, bytes, failures, skipped, manifest)."""
    checked: List[JunkItem] = []
    skipped: List[str] = []
    for item in items:
        try:
            assert_cleanable(item.path)
        except SafetyError as exc:
            skipped.append(f"{item.path}: {exc}")
            continue
        checked.append(item)

    removed: List[JunkItem] = []
    failures: List[str] = []
    for index in range(0, len(checked), batch_size):
        batch = checked[index : index + batch_size]
        listing = " ".join(_quote(item.path) for item in batch)
        output = adb.sh(
            f"rm -rf -- {listing}; for d in {listing}; do [ -e \"$d\" ] && echo \"$d\"; done",
            check=False,
        )
        still_there = {line.strip() for line in output.splitlines() if line.strip()}
        for item in batch:
            if item.path in still_there:
                failures.append(item.path)
            else:
                removed.append(item)

    manifest = None
    if record_manifest and removed:
        manifest = write_manifest(
            "junk-dirs",
            ["path", "bytes", "files"],
            [[item.path, str(item.size), str(item.files)] for item in removed],
        )
    return len(removed), sum(item.size for item in removed), failures, skipped, manifest


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
