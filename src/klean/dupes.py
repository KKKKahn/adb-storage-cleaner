"""Exact-duplicate detection: same size -> device-side MD5 -> keep policy."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .adb import ADB
from .classify import is_auto_deletable_app_file, keep_score
from .util import read_json, state_dir, write_json, write_manifest

HASH_RE = re.compile(r"^([0-9a-fA-F]{32})\s+(.+)$")


@dataclass
class DupGroup:
    digest: str
    size: int
    keeper: str
    deletable: List[str] = field(default_factory=list)
    blocked: List[str] = field(default_factory=list)

    @property
    def savings(self) -> int:
        return self.size * len(self.deletable)

    @property
    def copies(self) -> int:
        return 1 + len(self.deletable) + len(self.blocked)


def candidate_groups(entries: Iterable, min_size: int) -> Dict[int, List[str]]:
    by_size: Dict[int, List[str]] = {}
    for entry in entries:
        if getattr(entry, "is_dir", False):
            continue
        if entry.size < min_size:
            continue
        by_size.setdefault(entry.size, []).append(entry.path)
    return {size: sorted(paths) for size, paths in by_size.items() if len(paths) > 1}


def parse_hash_line(line: str) -> Optional[Tuple[str, str]]:
    """Return (digest, path) or None. Skips md5sum's escaped-name lines."""
    line = line.rstrip("\r\n")
    if not line or line.startswith("\\"):
        return None
    if len(line) >= 34 and line[32:34] == "  " and re.fullmatch(r"[0-9a-fA-F]{32}", line[:32]):
        return line[:32].lower(), line[34:]
    match = HASH_RE.match(line)
    if match:
        return match.group(1).lower(), match.group(2)
    return None


def hash_paths(
    adb: ADB,
    paths: Sequence[str],
    sizes: Dict[str, int],
    cache: Optional[Dict[str, Dict[str, object]]] = None,
    batch_size: int = 20,
    force: bool = False,
    progress=None,
    token: Optional[str] = None,
) -> Dict[str, str]:
    """Hash files on the device; reuse cache entries from the same scan only.

    `token` is normally the scan timestamp. A cache entry is trusted only when
    it was produced by the same scan, so a file rewritten in place (same size,
    new content) is never treated as a duplicate based on stale data.
    """
    cache = cache if cache is not None else {}
    result: Dict[str, str] = {}
    todo: List[str] = []
    for path in paths:
        cached = cache.get(path)
        if (
            not force
            and token
            and isinstance(cached, dict)
            and cached.get("t") == token
            and cached.get("s") == sizes.get(path)
            and isinstance(cached.get("h"), str)
        ):
            result[path] = str(cached["h"])
        else:
            todo.append(path)

    for index in range(0, len(todo), batch_size):
        batch = todo[index : index + batch_size]
        command = "md5sum " + " ".join(adb_quote(path) for path in batch)
        text = adb.sh(command, timeout=600, check=False)
        for line in text.splitlines():
            parsed = parse_hash_line(line)
            if not parsed:
                continue
            digest, path = parsed
            if path not in sizes:
                continue
            result[path] = digest
            cache[path] = {"s": sizes[path], "h": digest, "t": token or ""}
        if progress:
            progress(min(index + batch_size, len(todo)), len(todo))
    return result


def adb_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def build_groups(
    hashes: Dict[str, str],
    sizes: Dict[str, int],
    include_app_data: bool = False,
) -> List[DupGroup]:
    by_digest: Dict[str, List[str]] = {}
    for path, digest in hashes.items():
        by_digest.setdefault(digest, []).append(path)

    groups: List[DupGroup] = []
    for digest, paths in by_digest.items():
        if len(paths) < 2:
            continue
        keeper = min(paths, key=keep_score)
        deletable: List[str] = []
        blocked: List[str] = []
        for path in paths:
            if path == keeper:
                continue
            if not include_app_data and not is_auto_deletable_app_file(path):
                blocked.append(path)
            else:
                deletable.append(path)
        size = sizes.get(keeper, 0)
        groups.append(DupGroup(digest, size, keeper, sorted(deletable), sorted(blocked)))
    groups.sort(key=lambda group: (-group.savings, group.keeper))
    return groups


def _remote_missing(adb: ADB, paths: Sequence[str], batch_size: int = 40) -> set:
    missing = set()
    for index in range(0, len(paths), batch_size):
        batch = paths[index : index + batch_size]
        listing = " ".join(adb_quote(path) for path in batch)
        text = adb.sh(f'for f in {listing}; do [ -e "$f" ] || echo "$f"; done', check=False)
        missing.update(line.strip() for line in text.splitlines() if line.strip())
    return missing


def verify_keepers(
    adb: ADB,
    groups: Sequence[DupGroup],
    sizes: Dict[str, int],
    cache: Optional[Dict[str, Dict[str, object]]] = None,
    token: Optional[str] = None,
) -> Tuple[List[DupGroup], List[DupGroup]]:
    keepers = [group.keeper for group in groups]
    missing = _remote_missing(adb, keepers)
    alive = [group for group in groups if group.keeper not in missing]
    bad: List[DupGroup] = [group for group in groups if group.keeper in missing]
    if not alive:
        return [], bad
    hashes = hash_paths(
        adb,
        [group.keeper for group in alive],
        sizes,
        cache=cache,
        force=True,
        token=token,
    )
    good: List[DupGroup] = []
    for group in alive:
        if hashes.get(group.keeper) == group.digest:
            good.append(group)
        else:
            bad.append(group)
    return good, bad


def apply_groups(
    adb: ADB,
    groups: Sequence[DupGroup],
    batch_size: int = 20,
    record_manifest: bool = True,
) -> Tuple[int, int, List[str], Optional[str]]:
    """Delete every copy except the keeper. Returns (files, bytes, failures, manifest)."""
    owner: Dict[str, DupGroup] = {}
    for group in groups:
        for path in group.deletable:
            owner[path] = group
    targets = sorted(owner)
    if not targets:
        return 0, 0, [], None

    missing_keepers = _remote_missing(adb, [group.keeper for group in groups])
    targets = [path for path in targets if owner[path].keeper not in missing_keepers]
    deleted_paths: List[str] = []
    failures: List[str] = []
    for index in range(0, len(targets), batch_size):
        batch = targets[index : index + batch_size]
        listing = " ".join(adb_quote(path) for path in batch)
        output = adb.sh(
            f"rm -f -- {listing}; for f in {listing}; do [ -e \"$f\" ] && echo \"$f\"; done",
            check=False,
        )
        still_there = {line.strip() for line in output.splitlines() if line.strip()}
        for path in batch:
            if path in still_there:
                failures.append(path)
            else:
                deleted_paths.append(path)

    freed = sum(owner[path].size for path in deleted_paths)
    manifest = None
    if record_manifest and deleted_paths:
        rows = [
            [owner[path].digest, str(owner[path].size), owner[path].keeper, path]
            for path in deleted_paths
        ]
        manifest = write_manifest(
            "exact-duplicates",
            ["md5", "bytes", "kept", "deleted"],
            rows,
        )
    return len(deleted_paths), freed, failures, manifest


def hash_cache_path(serial: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", serial or "unknown")
    return state_dir() / f"hashes-{safe}.json"


def load_hash_cache(serial: str) -> Dict[str, Dict[str, object]]:
    payload = read_json(hash_cache_path(serial), {})
    return payload if isinstance(payload, dict) else {}


def save_hash_cache(serial: str, cache: Dict[str, Dict[str, object]]) -> None:
    write_json(hash_cache_path(serial), cache)


def human_groups(groups: Sequence[DupGroup]) -> List[str]:
    return [
        f"{group.size} B x {group.copies}  keep={group.keeper}"  # debug helper
        for group in groups
    ]
