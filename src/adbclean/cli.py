"""Command line interface for adbclean."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, Optional, Sequence

from . import __version__
from .adb import ADB, ADBError
from .apps import usage_table
from .clean import apply as apply_junk
from .clean import plan as plan_junk
from .clean import total_size as junk_total
from .dupes import (
    apply_groups,
    build_groups,
    candidate_groups,
    hash_paths,
    load_hash_cache,
    save_hash_cache,
    verify_keepers,
)
from .live import (
    backup_pairs,
    delete_pairs,
    find_pairs,
    load_durations,
    month_stats,
)
from .live import (
    total_size as live_total,
)
from .report import build as build_report
from .report import write_reports
from .scan import Scan, load_latest, save_scan, scan_device
from .util import (
    SafetyError,
    Style,
    color_enabled,
    confirm,
    format_int,
    human,
    parse_size,
    percent,
    print_error,
    print_info,
    print_warn,
    shorten,
    table,
)

STATE_LABELS = {
    "device": "已连接",
    "unauthorized": "未授权",
    "offline": "离线",
    "no permissions": "无权限",
}


def _common() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--serial", default=argparse.SUPPRESS, help="设备序列号（多设备时必填）")
    parser.add_argument("--adb", default=argparse.SUPPRESS, help="adb 可执行文件路径（默认 adb）")
    parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="禁用彩色输出")
    parser.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="输出更多细节")
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="adbclean",
        description="不 Root、纯 ADB 的安卓存储审计与安全清理工具。默认只读。",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"adbclean {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="命令")

    p = sub.add_parser("devices", parents=[common], help="列出已连接设备")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("info", parents=[common], help="显示设备与存储概览")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("scan", parents=[common], help="扫描 /sdcard 并缓存文件清单")
    p.add_argument("--root", default="/sdcard", help="扫描根目录（默认 /sdcard）")
    p.add_argument("--timeout", type=int, default=900, help="扫描超时秒数（默认 900）")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("report", parents=[common], help="生成 Markdown / HTML 报告")
    p.add_argument("--cached", action="store_true", help="使用上次扫描缓存，不再重新扫描")
    p.add_argument("--out", default="reports", help="报告输出目录（默认 reports）")
    p.add_argument("--no-html", action="store_true", help="只生成 Markdown")
    p.add_argument("--no-live", action="store_true", help="跳过动态照片分析")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("junk", parents=[common], help="清理白名单缓存/缩略图（默认 dry-run）")
    p.add_argument("--cached", action="store_true", help="使用上次扫描缓存")
    p.add_argument("--apply", action="store_true", help="真正执行删除")
    p.add_argument("--yes", action="store_true", help="跳过确认")
    p.set_defaults(func=cmd_junk)

    p = sub.add_parser("dupes", parents=[common], help="查找并删除字节级重复文件（默认 dry-run）")
    p.add_argument("--cached", action="store_true", help="使用上次扫描缓存")
    p.add_argument("--min-size", default="1M", help="参与检测的最小体积（默认 1M）")
    p.add_argument("--apply", action="store_true", help="真正执行删除")
    p.add_argument("--yes", action="store_true", help="跳过确认")
    p.add_argument(
        "--include-app-data",
        action="store_true",
        help="允许删除 Android/data 中的非缓存副本（风险更高，默认关闭）",
    )
    p.add_argument("--no-verify", action="store_true", help="删除前不重新校验保留副本的 MD5")
    p.set_defaults(func=cmd_dupes)

    p = sub.add_parser("live", parents=[common], help="统计动态照片伴生 MP4（默认只读）")
    p.add_argument("--cached", action="store_true", help="使用上次扫描缓存")
    p.add_argument("--delete", action="store_true", help="删除伴生 MP4，保留 JPG")
    p.add_argument("--yes", action="store_true", help="跳过确认")
    p.add_argument("--backup", help="删除前把 MP4 备份到该目录")
    p.add_argument("--max-duration", type=int, default=5000, help="视为动态照片的最长时长，毫秒（默认 5000）")
    p.add_argument(
        "--include-unknown",
        action="store_true",
        help="包含 MediaStore 查不到时长、但命名符合的 mp4（默认跳过）",
    )
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("apps", parents=[common], help="按应用统计外部存储占用")
    p.add_argument("--cached", action="store_true", help="使用上次扫描缓存")
    p.set_defaults(func=cmd_apps)

    p = sub.add_parser("studio", parents=[common], help="启动实况照片压缩 Web 工作台")
    p.add_argument("--cached", action="store_true", help="强制使用上次扫描缓存")
    p.add_argument("--rescan", action="store_true", help="启动前重新扫描 /sdcard")
    p.add_argument("--port", type=int, default=8765, help="本地端口（默认 8765）")
    p.add_argument("--host", default="127.0.0.1", help="监听地址，仅允许本机（默认 127.0.0.1）")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.add_argument("--max-duration", type=int, default=5000, help="实况 MP4 最大时长，毫秒（默认 5000）")
    p.add_argument(
        "--include-unknown",
        action="store_true",
        help="包含 MediaStore 查不到时长的配对（默认跳过）",
    )
    p.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 路径（默认 ffmpeg）")
    p.add_argument("--ffprobe", default="ffprobe", help="ffprobe 路径（默认 ffprobe）")
    p.set_defaults(func=cmd_studio)

    p = sub.add_parser("all", parents=[common], help="扫描 + 报告 + 清理建议（只读）")
    p.add_argument("--out", default="reports", help="报告输出目录（默认 reports）")
    p.set_defaults(func=cmd_all)

    return parser


# --------------------------------------------------------------------- helpers
def _style(args) -> Style:
    return Style(color_enabled(getattr(args, "no_color", False)))


def _connect(args) -> ADB:
    adb = ADB.connect(getattr(args, "adb", None), getattr(args, "serial", None))
    if getattr(args, "verbose", False):
        print_info(f"使用设备 {adb.serial}", _style(args))
    return adb


def _scan(args, adb: ADB) -> Scan:
    if getattr(args, "cached", False):
        scan = load_latest(adb.serial)
        if scan is None:
            raise ADBError("没有可用的扫描缓存。请先运行：adbclean scan")
        print_info(f"使用缓存清单：{scan.scanned_at}（{format_int(scan.total_files)} 个文件）", _style(args))
        return scan
    style = _style(args)
    print_info("正在扫描 /sdcard（通常 30–120 秒，请保持解锁）...", style)
    scan = scan_device(adb, getattr(args, "root", "/sdcard"), getattr(args, "timeout", 900))
    path = save_scan(scan)
    print_info(
        f"扫描完成：{format_int(scan.total_files)} 个文件 / {human(scan.total_size)}；缓存 {path}",
        style,
    )
    if getattr(args, "verbose", False):
        print_info(f"原始 du 行数：{format_int(scan.raw_lines)}", style)
    return scan


def _storage_table(df: Dict[str, int], style: Style) -> str:
    total = df.get("total_kb", 0) * 1024
    used = df.get("used_kb", 0) * 1024
    avail = df.get("avail_kb", 0) * 1024
    return table(
        ["总容量", "已用", "可用", "使用率"],
        [[human(total), human(used), human(avail), percent(used, total)]],
        ["r", "r", "r", "r"],
        style,
    )


# -------------------------------------------------------------------- commands
def cmd_devices(args) -> int:
    style = _style(args)
    devices = ADB.list_devices(getattr(args, "adb", "adb"))
    if not devices:
        print_warn("没有检测到设备。请检查 USB 调试和 RSA 授权。", style)
        return 0
    rows = [
        [
            device.serial,
            STATE_LABELS.get(device.state, device.state),
            device.model or "-",
            device.transport_id or "-",
        ]
        for device in devices
    ]
    print(table(["序列号", "状态", "型号", "transport"], rows, style=style))
    return 0


def cmd_info(args) -> int:
    style = _style(args)
    adb = _connect(args)
    props = adb.props()
    df = adb.df("/data")
    users = adb.users()
    diskstats = adb.diskstats()

    print(style.bold("设备"))
    print(f"  型号       {props.get('ro.product.brand', '')} {props.get('ro.product.model', '')}".rstrip())
    print(f"  系统       Android {props.get('ro.build.version.release', '?')} "
          f"(API {props.get('ro.build.version.sdk', '?')})")
    print(f"  Build      {props.get('ro.build.display.id', '?')}")
    print(f"  序列号     {adb.serial}")
    print()
    print(style.bold("存储（/data）"))
    print(_storage_table(df, style))
    print()
    if diskstats:
        print(style.bold("dumpsys diskstats（仅供参考，部分 ROM 会缓存旧值）"))
        labels = {
            "App Size": "应用本体",
            "App Data Size": "应用数据",
            "App Cache Size": "应用缓存",
            "Photos Size": "照片",
            "Videos Size": "视频",
            "Audio Size": "音频",
            "Downloads Size": "下载",
            "System Size": "系统（部分 ROM 为总容量）",
            "Other Size": "其他",
        }
        rows = []
        for key, label in labels.items():
            if key in diskstats:
                rows.append([label, human(diskstats[key])])
        if rows:
            print(table(["分类", "体积"], rows, ["l", "r"], style))
        print()
    if users:
        print(style.bold("用户"))
        if len(users) > 1:
            ids = ", ".join(str(user) for user in users)
            print(f"  检测到多用户/隐私空间：{ids}")
            print_warn("本工具只扫描 User 0 的 /sdcard，不会触碰隐私空间或应用分身。", style)
        else:
            print("  仅 User 0")
    return 0


def cmd_scan(args) -> int:
    style = _style(args)
    adb = _connect(args)
    print_info("正在扫描 /sdcard（通常 30–120 秒，请保持解锁）...", style)
    scan = scan_device(adb, args.root, args.timeout)
    path = save_scan(scan)
    print()
    print(table(
        ["文件数", "总体积", "扫描范围", "缓存文件"],
        [[format_int(scan.total_files), human(scan.total_size), scan.root, path]],
        ["r", "r", "l", "l"],
        style,
    ))
    return 0


def cmd_report(args) -> int:
    style = _style(args)
    adb = _connect(args)
    scan = _scan(args, adb)
    print_info("正在生成报告...", style)
    data = build_report(scan, adb, include_live=not args.no_live)
    paths = write_reports(data, args.out, want_html=not args.no_html)
    print()
    print(table(
        ["项目", "数值"],
        [
            ["文件总数", format_int(scan.total_files)],
            ["文件总体积", human(scan.total_size)],
            ["可清理缓存", human(data.junk_total)],
            ["动态照片 MP4", f"{len(data.live_pairs):,} 个 / {human(live_total(data.live_pairs))}"],
        ],
        ["l", "r"],
        style,
    ))
    print()
    for path in paths:
        print(style.green("✓") + " " + os.path.abspath(path))
    return 0


def cmd_junk(args) -> int:
    style = _style(args)
    adb = _connect(args)
    scan = _scan(args, adb)
    if args.cached:
        print_warn("使用缓存清单，目录体积可能已变化；删除前仍会重新校验白名单。", style)
    items = plan_junk(scan)
    total = junk_total(items)
    if not items:
        print_info("没有发现白名单内的缓存/缩略图目录。", style)
        return 0
    rows = [[shorten(item.path, 82), f"{item.files:,}", human(item.size)] for item in items[:30]]
    print(table(["目录", "文件数", "体积"], rows, ["l", "r", "r"], style))
    if len(items) > 30:
        print(style.dim(f"  ... 另有 {len(items) - 30} 个目录"))
    print()
    print(style.bold(f"合计 {len(items)} 个目录 / {human(total)}"))
    if not args.apply:
        print_info("dry-run：未删除任何文件。确认后加 --apply 执行。", style)
        return 0
    print_warn("缓存目录会被应用重新生成；删除后短期内有助释放空间，但收益可能回落。", style)
    if not confirm(f"确认删除这 {len(items)} 个目录（{human(total)}）？", args.yes):
        print_info("已取消。", style)
        return 0
    before = adb.df("/data").get("used_kb", 0) * 1024
    removed, freed, failures, skipped, manifest = apply_junk(adb, items)
    after = adb.df("/data").get("used_kb", 0) * 1024
    print()
    print(style.green("✓") + f" 已删除 {removed} 个目录 / {human(freed)}")
    print(f"  df 实际变化：{human(max(0, before - after))}")
    if failures:
        print_warn(f"{len(failures)} 个目录删除失败（可能被应用占用），可重启后重试：", style)
        for path in failures[:10]:
            print("   " + path)
    if skipped:
        print_warn(f"{len(skipped)} 个目录被安全护栏跳过。", style)
    if manifest:
        print_info(f"删除清单：{manifest}", style)
    return 0


def cmd_dupes(args) -> int:
    style = _style(args)
    adb = _connect(args)
    scan = _scan(args, adb)
    try:
        min_size = parse_size(args.min_size)
    except ValueError as exc:
        raise ADBError(str(exc)) from exc
    if min_size < 0:
        raise ADBError("--min-size 不能为负数")

    sizes = {entry.path: entry.size for entry in scan.files}
    groups_by_size = candidate_groups(scan.entries, min_size)
    candidate_paths = [path for paths in groups_by_size.values() for path in paths]
    candidate_bytes = sum(sizes[path] for path in candidate_paths)
    if not groups_by_size:
        print_info("没有发现同大小的候选文件。", style)
        return 0
    print_info(
        f"同大小候选：{len(groups_by_size):,} 组 / {len(candidate_paths):,} 个文件 / "
        f"{human(candidate_bytes)}，开始计算 MD5...",
        style,
    )
    cache = load_hash_cache(adb.serial)
    last = [0]

    def progress(done: int, total: int) -> None:
        if done == total or done - last[0] >= 200:
            print(style.dim(f"  哈希 {done:,}/{total:,}"))
            last[0] = done

    hashes = hash_paths(
        adb,
        candidate_paths,
        sizes,
        cache=cache,
        progress=progress,
        token=scan.scanned_at,
    )
    save_hash_cache(adb.serial, cache)
    groups = build_groups(hashes, sizes, include_app_data=args.include_app_data)
    deletable = sum(len(group.deletable) for group in groups)
    savings = sum(group.savings for group in groups)
    blocked = sum(len(group.blocked) for group in groups)
    blocked_bytes = sum(group.size * len(group.blocked) for group in groups)

    if not groups:
        print_info("没有发现字节级完全相同的重复文件。", style)
        return 0
    print()
    rows = []
    for group in groups[:15]:
        rows.append(
            [
                shorten(group.keeper, 68),
                str(group.copies),
                human(group.size),
                human(group.savings),
            ]
        )
    print(table(["保留（体积最大的 15 组）", "份数", "单份", "可释放"], rows, ["l", "r", "r", "r"], style))
    print()
    print(style.bold(
        f"共 {len(groups):,} 组；可自动删除 {deletable:,} 个 / {human(savings)}"
    ))
    if blocked:
        print_warn(
            f"另有 {blocked:,} 个副本位于 Android/data 的非缓存目录（{human(blocked_bytes)}），"
            "已保护；如确认可加 --include-app-data。",
            style,
        )
    if not args.apply:
        print_info("dry-run：未删除任何文件。确认后加 --apply 执行。", style)
        return 0

    if not args.no_verify:
        print_info("删除前重新校验保留副本的 MD5...", style)
        groups, bad = verify_keepers(adb, groups, sizes, cache, token=scan.scanned_at)
        if bad:
            print_warn(f"{len(bad)} 组保留副本已变化，本次跳过。", style)
        save_hash_cache(adb.serial, cache)
    if not groups:
        print_info("没有可安全删除的组。", style)
        return 0
    total = sum(group.savings for group in groups)
    if not confirm(f"确认删除 {sum(len(g.deletable) for g in groups):,} 个重复副本（{human(total)}）？", args.yes):
        print_info("已取消。", style)
        return 0
    before = adb.df("/data").get("used_kb", 0) * 1024
    deleted, freed, failures, manifest = apply_groups(adb, groups)
    after = adb.df("/data").get("used_kb", 0) * 1024
    print()
    print(style.green("✓") + f" 已删除 {deleted:,} 个重复文件 / {human(freed)}")
    print(f"  df 实际变化：{human(max(0, before - after))}")
    if failures:
        print_warn(f"{len(failures)} 个文件删除失败：", style)
        for path in failures[:10]:
            print("   " + path)
    if manifest:
        print_info(f"删除清单：{manifest}", style)
    return 0


def cmd_live(args) -> int:
    style = _style(args)
    adb = _connect(args)
    scan = _scan(args, adb)
    print_info("读取 MediaStore 视频时长...", style)
    durations = load_durations(adb)
    durations_available = bool(durations)
    if not durations_available:
        print_warn(
            "无法读取 MediaStore 时长；默认不删除任何文件。"
            "只有显式加 --include-unknown 才会按文件名处理。",
            style,
        )
    pairs, long_pairs, unknown = find_pairs(
        scan.entries,
        durations if durations_available else {},
        max_duration_ms=args.max_duration,
        include_unknown=args.include_unknown,
    )
    if not pairs and not long_pairs and not unknown:
        print_info("没有发现动态照片伴生 MP4。", style)
        return 0
    rows = [
        [month, f"{count:,}", human(size)]
        for month, count, size in month_stats(pairs)
    ]
    print()
    print(table(["年月", "数量", "体积"], rows, ["l", "r", "r"], style))
    print()
    print(style.bold(f"动态照片 MP4：{len(pairs):,} 个 / {human(live_total(pairs))}"))
    if long_pairs:
        print(f"  时长 > {args.max_duration}ms，已排除：{len(long_pairs):,} 个 / {human(live_total(long_pairs))}")
    if unknown:
        suffix = "已包含" if args.include_unknown else "默认跳过，加 --include-unknown 可包含"
        print(f"  无法读取时长：{len(unknown):,} 个 / {human(live_total(unknown))}（{suffix}）")
    print()
    sample = sorted(pairs, key=lambda item: -item.size)[:8]
    if sample:
        print(style.dim("体积最大的 8 个："))
        for pair in sample:
            print(f"  {pair.date} {pair.time}  {human(pair.size):>9}  {pair.mp4}")
        print()

    if not args.delete:
        print_info(
            "只读模式。删除伴生 MP4 会永久失去照片动效（JPG 仍保留）："
            "adbclean live --delete --backup <目录>",
            style,
        )
        return 0

    if not pairs:
        print_info("没有可删除的 MP4。", style)
        return 0
    if not durations_available:
        print_warn("未做时长校验：所有命名匹配的 MP4 都可能被删除。", style)
    print_warn("删除后照片仍在，但长按/上滑不再有动态效果；此操作不可撤销（除非已备份）。", style)
    backup_dir = os.path.abspath(args.backup) if args.backup else None
    if backup_dir:
        print_info(f"删除前备份到：{backup_dir}", style)
    else:
        print_warn("未指定 --backup：删除后无法从本机恢复这些 MP4。", style)
    if not confirm(f"确认删除 {len(pairs):,} 个 MP4（{human(live_total(pairs))}），并保留全部 JPG？", args.yes):
        print_info("已取消。", style)
        return 0

    to_delete = pairs
    restore_script = None
    if backup_dir:
        done = [0]

        def progress(index: int, total: int) -> None:
            if index == total or index - done[0] >= 100:
                print(style.dim(f"  备份 {index:,}/{total:,}"))
                done[0] = index

        backed_up, backup_failures, restore_script = backup_pairs(
            adb, pairs, backup_dir, progress=progress
        )
        if backup_failures:
            print_warn(f"{len(backup_failures)} 个文件备份失败，将保留不删。", style)
            for pair, reason in backup_failures[:5]:
                print(f"   {pair.mp4}: {reason}")
        to_delete = backed_up
        if not to_delete:
            print_error("没有任何文件备份成功，已中止。", style)
            return 1
        print_info(f"备份完成：{len(backed_up):,} 个；恢复脚本 {restore_script}", style)

    before = adb.df("/data").get("used_kb", 0) * 1024
    deleted, freed, failures, skipped, manifest = delete_pairs(adb, to_delete)
    after = adb.df("/data").get("used_kb", 0) * 1024
    print()
    print(style.green("✓") + f" 已删除 {deleted:,} 个 MP4 / {human(freed)}，JPG 全部保留")
    print(f"  df 实际变化：{human(max(0, before - after))}")
    if failures:
        print_warn(f"{len(failures)} 个删除失败：", style)
        for path in failures[:10]:
            print("   " + path)
    if skipped:
        print_warn(f"{len(skipped)} 个因 JPG 缺失被跳过。", style)
    if manifest:
        print_info(f"删除清单：{manifest}", style)
    print_info("相册若仍显示旧缩略图，重启手机或相册即可刷新。", style)
    return 0


def cmd_apps(args) -> int:
    style = _style(args)
    adb = _connect(args)
    scan = _scan(args, adb)
    rows = usage_table(scan, adb)
    if not rows:
        print_info("没有读到 /sdcard/Android/data 的占用。", style)
        return 0
    total = sum(row.size for row in rows)
    display = [[row.package, row.kind, human(row.size)] for row in rows[:40]]
    print(table(["包名", "类型", "外部数据"], display, ["l", "l", "r"], style))
    if len(rows) > 40:
        print(style.dim(f"  ... 另有 {len(rows) - 40} 个应用"))
    print()
    print(style.bold(f"合计 {len(rows)} 个应用 / {human(total)}"))
    print_info(
        "外部数据只统计 /sdcard/Android/data 与 /obb，不含应用内部 /data/data。",
        style,
    )
    return 0


def cmd_studio(args) -> int:
    from .studio import serve

    return serve(args)


def cmd_all(args) -> int:
    style = _style(args)
    adb = _connect(args)
    scan = _scan(args, adb)
    print_info("正在生成报告...", style)
    data = build_report(scan, adb, include_live=True)
    paths = write_reports(data, args.out, want_html=True)
    items = plan_junk(scan)
    print()
    print(table(
        ["项目", "数值"],
        [
            ["文件总数", format_int(scan.total_files)],
            ["文件总体积", human(scan.total_size)],
            ["分类数", str(len(data.categories))],
            ["可清理缓存", f"{len(items)} 个目录 / {human(junk_total(items))}"],
            ["动态照片 MP4", f"{len(data.live_pairs):,} 个 / {human(live_total(data.live_pairs))}"],
        ],
        ["l", "r"],
        style,
    ))
    print()
    for path in paths:
        print(style.green("✓") + " " + os.path.abspath(path))
    print_info("全部操作均为只读；需要清理时运行 adbclean junk --apply。", style)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ADBError as exc:
        print_error(str(exc), _style(args))
        if getattr(args, "verbose", False) and exc.command:
            print_error("命令: " + " ".join(exc.command), _style(args))
        return 1
    except SafetyError as exc:
        print_error(f"安全护栏拦截：{exc}", _style(args))
        return 2
    except ValueError as exc:
        print_error(str(exc), _style(args))
        return 1
    except KeyboardInterrupt:
        print()
        print_error("已中断。", _style(args))
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
