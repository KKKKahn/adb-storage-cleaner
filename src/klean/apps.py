"""Per-app external storage usage (/sdcard/Android/data and /obb)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from .adb import ADB
from .scan import Scan


@dataclass
class AppUsage:
    package: str
    size: int
    kind: str = "未知"


def external_sizes(scan: Scan) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    for entry in scan.files:
        if entry.path.startswith("/sdcard/Android/data/"):
            rest = entry.path[len("/sdcard/Android/data/") :]
        elif entry.path.startswith("/sdcard/Android/obb/"):
            rest = entry.path[len("/sdcard/Android/obb/") :]
        else:
            continue
        package = rest.split("/", 1)[0]
        if not package:
            continue
        totals[package] = totals.get(package, 0) + entry.size
    return totals


def installed(adb: ADB) -> Tuple[Set[str], Set[str]]:
    def parse(text: str) -> Set[str]:
        packages = set()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                packages.add(line.split(":", 1)[1].strip())
        return packages

    user_text = adb.sh("pm list packages -3", check=False)
    system_text = adb.sh("pm list packages -s", check=False)
    return parse(user_text), parse(system_text)


def usage_table(scan: Scan, adb: ADB) -> List[AppUsage]:
    sizes = external_sizes(scan)
    user, system = installed(adb)
    rows: List[AppUsage] = []
    for package, size in sizes.items():
        if package in user:
            kind = "用户应用"
        elif package in system:
            kind = "系统应用"
        else:
            kind = "未知/已卸载"
        rows.append(AppUsage(package=package, size=size, kind=kind))
    rows.sort(key=lambda row: -row.size)
    return rows
