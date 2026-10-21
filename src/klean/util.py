"""Small shared helpers: formatting, colours, sizes, cache paths, manifests."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_MIGRATED_BASES: set = set()


class SafetyError(RuntimeError):
    """Raised when an operation would touch something outside the safe whitelist."""


def color_enabled(no_color: bool = False) -> bool:
    if no_color or os.environ.get("NO_COLOR"):
        return False
    try:
        return sys.stdout.isatty()
    except Exception:  # pragma: no cover - exotic stdout objects
        return False


class Style:
    """Tiny ANSI helper; degrades to plain text when disabled."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)

    def magenta(self, text: str) -> str:
        return self._wrap("35", text)


def cell_width(text: str) -> int:
    """Display width in terminal cells (CJK counts as two)."""
    text = _ANSI_RE.sub("", text)
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _pad(text: str, width: int, align: str) -> str:
    gap = max(0, width - cell_width(text))
    if align == "r":
        return " " * gap + text
    if align == "c":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def table(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    aligns: Optional[Sequence[str]] = None,
    style: Optional[Style] = None,
) -> str:
    """Render a plain-text table that stays aligned with Chinese characters."""
    style = style or Style(False)
    header_cells = [str(h) for h in headers]
    body = [[str(cell) for cell in row] for row in rows]
    columns = len(header_cells)
    if columns == 0:
        return ""
    aligns = list(aligns or ["l"] * columns)
    widths = [cell_width(h) for h in header_cells]
    for row in body:
        for index in range(min(columns, len(row))):
            widths[index] = max(widths[index], cell_width(row[index]))

    def render(cells: Sequence[str], header: bool = False) -> str:
        padded = [
            _pad(cells[i] if i < len(cells) else "", widths[i], aligns[i])
            for i in range(columns)
        ]
        line = "  ".join(padded).rstrip()
        return style.bold(line) if header else line

    lines = [render(header_cells, header=True)]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend(render(row) for row in body)
    return "\n".join(lines)


def human(size: float, binary: bool = False, digits: int = 2) -> str:
    """Human readable size. Decimal units by default to match phone Settings."""
    base = 1024.0 if binary else 1000.0
    units = ["B", "KiB", "MiB", "GiB", "TiB"] if binary else ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    index = 0
    while abs(value) >= base and index < len(units) - 1:
        value /= base
        index += 1
    if index == 0:
        return f"{int(value)} {units[index]}"
    return f"{value:.{digits}f} {units[index]}"


def percent(part: float, total: float) -> str:
    if total <= 0:
        return "0.0%"
    return f"{part * 100.0 / total:.1f}%"


_SIZE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(B|K|KB|KIB|M|MB|MIB|G|GB|GIB|T|TB|TIB)?\s*$",
    re.IGNORECASE,
)

_SIZE_UNITS = {
    "": 1,
    "B": 1,
    "K": 1000,
    "KB": 1000,
    "KIB": 1024,
    "M": 1000**2,
    "MB": 1000**2,
    "MIB": 1024**2,
    "G": 1000**3,
    "GB": 1000**3,
    "GIB": 1024**3,
    "T": 1000**4,
    "TB": 1000**4,
    "TIB": 1024**4,
}


def parse_size(text: str) -> int:
    match = _SIZE_RE.match(text)
    if not match:
        raise ValueError(f"无法识别的体积: {text!r}（示例: 500K, 1M, 2.5GB）")
    number, unit = match.group(1), (match.group(2) or "").upper()
    return int(float(number) * _SIZE_UNITS[unit])


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _migrate_legacy_dir(base: Path) -> None:
    """Move a pre-rename adbclean directory to the Klean path once."""
    if str(base) in _MIGRATED_BASES:
        return
    _MIGRATED_BASES.add(str(base))
    legacy = base / "adbclean"
    target = base / "klean"
    if not legacy.exists():
        return

    def merge(source: Path, destination: Path) -> None:
        if not destination.exists():
            with contextlib.suppress(OSError):
                source.rename(destination)
            return
        if source.is_dir() and destination.is_dir():
            for child in list(source.iterdir()):
                merge(child, destination / child.name)
            with contextlib.suppress(OSError):
                source.rmdir()

    merge(legacy, target)


def state_dir() -> Path:
    """Per-user cache/state directory; created on demand."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    _migrate_legacy_dir(base)
    path = base / "klean"
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    """Persistent per-user data (backups). Never a cache directory."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    _migrate_legacy_dir(base)
    path = base / "klean"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as handle:
            handle.write(data)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path: Path, payload: Any) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8", errors="surrogateescape") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_manifest(kind: str, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """Append-only TSV record of every destructive action, for traceability."""
    directory = state_dir() / "manifests"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp()}-{kind}.tsv"
    counter = 1
    while path.exists():
        counter += 1
        path = directory / f"{stamp()}-{kind}-{counter}.tsv"
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as handle:
        handle.write("\t".join(str(item) for item in header) + "\n")
        for row in rows:
            safe = [str(item).replace("\t", " ").replace("\n", " ") for item in row]
            handle.write("\t".join(safe) + "\n")
    return str(path)


def confirm(question: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes", "是", "确认")


def shorten(text: str, width: int = 72) -> str:
    if len(text) <= width:
        return text
    keep = max(10, (width - 3) // 2)
    return text[:keep] + "..." + text[-(width - 3 - keep) :]


def mask_serial(serial: str) -> str:
    if not serial:
        return "unknown"
    if len(serial) <= 6:
        return serial[:2] + "***"
    return serial[:4] + "***" + serial[-2:]


def print_info(message: str, style: Optional[Style] = None) -> None:
    style = style or Style(False)
    print(style.cyan("[i]") + " " + message)


def print_warn(message: str, style: Optional[Style] = None) -> None:
    style = style or Style(False)
    print(style.yellow("[!]") + " " + message)


def print_error(message: str, style: Optional[Style] = None) -> None:
    style = style or Style(False)
    print(style.red("[x]") + " " + message, file=sys.stderr)


def format_int(value: int) -> str:
    return f"{value:,}"


def md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(h) for h in headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        cells = [str(cell).replace("|", "\\|").replace("\n", " ") for cell in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def epoch_seconds() -> int:
    return int(dt.datetime.now().timestamp())
