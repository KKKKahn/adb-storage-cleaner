"""Build Markdown/HTML storage reports from a scan."""

from __future__ import annotations

import html
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from .adb import ADB
from .classify import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    file_category,
    junk_dirs,
    top_level_dir,
)
from .live import LivePair, find_pairs, load_durations, month_stats
from .live import total_size as live_size
from .scan import Scan
from .util import human, mask_serial, md_table, now_iso, percent, stamp

LARGE_FILE_THRESHOLD = 200_000_000


@dataclass
class ReportData:
    generated_at: str
    device: Dict[str, str]
    storage: Dict[str, int]
    users: List[int]
    scan: Scan
    categories: Dict[str, Tuple[int, int]]
    top_dirs: List[Tuple[str, int]]
    junk: List[Tuple[str, int, int]]
    junk_total: int
    live_pairs: List[LivePair] = field(default_factory=list)
    live_long: List[LivePair] = field(default_factory=list)
    live_unknown: List[LivePair] = field(default_factory=list)
    live_duration_available: bool = True
    large_files: List[Tuple[str, int]] = field(default_factory=list)


def build(scan: Scan, adb: ADB, include_live: bool = True) -> ReportData:
    props = adb.props()
    storage = adb.df("/data")
    users = adb.users()

    categories: Dict[str, Tuple[int, int]] = {}
    for entry in scan.files:
        key = file_category(entry.path)
        size, count = categories.get(key, (0, 0))
        categories[key] = (size + entry.size, count + 1)

    dir_totals: Dict[str, int] = defaultdict(int)
    for entry in scan.files:
        dir_totals[top_level_dir(entry.path)] += entry.size
    top_dirs = sorted(dir_totals.items(), key=lambda item: -item[1])[:20]

    junk_rows = sorted(junk_dirs(scan.entries), key=lambda row: -row[1])
    junk_total = sum(row[1] for row in junk_rows)

    large = [(entry.path, entry.size) for entry in scan.files if entry.size >= LARGE_FILE_THRESHOLD]
    large.sort(key=lambda item: -item[1])

    pairs: List[LivePair] = []
    long_pairs: List[LivePair] = []
    unknown: List[LivePair] = []
    duration_available = True
    if include_live:
        durations = load_durations(adb)
        duration_available = bool(durations)
        if duration_available:
            pairs, long_pairs, unknown = find_pairs(
                scan.entries, durations, max_duration_ms=5000, include_unknown=False
            )
        else:
            # MediaStore unavailable: show the pairs, but mark the report so the
            # lack of duration filtering is explicit.
            pairs, long_pairs, unknown = find_pairs(scan.entries, None)
    pairs.sort(key=lambda item: -item.size)

    return ReportData(
        generated_at=now_iso(),
        device=props,
        storage=storage,
        users=users,
        scan=scan,
        categories=categories,
        top_dirs=top_dirs,
        junk=[(row[0], row[1], row[2]) for row in junk_rows[:30]],
        junk_total=junk_total,
        live_pairs=pairs,
        live_long=long_pairs,
        live_unknown=unknown,
        live_duration_available=duration_available,
        large_files=large[:20],
    )


def _device_name(data: ReportData) -> str:
    brand = data.device.get("ro.product.brand", "").strip()
    model = data.device.get("ro.product.model", "").strip()
    name = " ".join(part for part in (brand, model) if part)
    return name or "Android 设备"


def render_markdown(data: ReportData) -> str:
    total = data.scan.total_size
    lines: List[str] = []
    lines.append("# 安卓存储审计报告")
    lines.append("")
    lines.append(f"- **设备**：{_device_name(data)}")
    lines.append(f"- **序列号**：`{mask_serial(data.scan.serial)}`")
    lines.append(
        f"- **系统**：Android {data.device.get('ro.build.version.release', '?')} "
        f"(API {data.device.get('ro.build.version.sdk', '?')})"
    )
    lines.append(f"- **扫描时间**：{data.scan.scanned_at or data.generated_at}")
    lines.append(
        f"- **扫描范围**：`{data.scan.root}`，{data.scan.total_files:,} 个文件，"
        f"{human(total)}"
    )
    lines.append("")
    lines.append("> 体积使用十进制单位（1 GB = 10^9 字节），与手机设置页一致。")
    lines.append("")

    lines.append("## 存储总览")
    lines.append("")
    storage = data.storage
    total_bytes = storage.get("total_kb", 0) * 1024
    used_bytes = storage.get("used_kb", 0) * 1024
    avail_bytes = storage.get("avail_kb", 0) * 1024
    lines.append(
        md_table(
            ["总容量", "已用", "可用", "使用率"],
            [
                [
                    human(total_bytes),
                    human(used_bytes),
                    human(avail_bytes),
                    percent(used_bytes, total_bytes),
                ]
            ],
        )
    )
    lines.append("")

    lines.append("## 文件分类")
    lines.append("")
    rows = []
    for key in CATEGORY_ORDER:
        size, count = data.categories.get(key, (0, 0))
        if key not in data.categories:
            continue
        rows.append(
            [
                CATEGORY_LABELS[key],
                f"{count:,}",
                human(size),
                percent(size, total),
            ]
        )
    lines.append(md_table(["分类", "文件数", "体积", "占比"], rows))
    lines.append("")
    lines.append("> 视频分类包含动态照片伴生 MP4；详见下方“动态照片伴生视频”。")
    lines.append("")

    lines.append("## 占用最大的目录（Top 20）")
    lines.append("")
    rows = [
        [path, human(size), percent(size, total)]
        for path, size in data.top_dirs
        if size > 0
    ]
    lines.append(md_table(["目录", "体积", "占比"], rows))
    lines.append("")

    lines.append("## 可安全清理项（白名单）")
    lines.append("")
    if data.junk:
        rows = [[path, f"{count:,}", human(size)] for path, size, count in data.junk]
        lines.append(md_table(["目录", "文件数", "体积"], rows))
        lines.append("")
        lines.append(f"**合计：{human(data.junk_total)}**（白名单只含缓存、缩略图、临时/日志目录）")
        lines.append("")
        lines.append("清理命令：`adbclean junk --apply`（默认 dry-run，不会自动执行）。")
    else:
        lines.append("未发现白名单内的缓存/缩略图目录。")
    lines.append("")

    if data.live_pairs or data.live_long or data.live_unknown:
        lines.append("## 动态照片伴生视频")
        lines.append("")
        lines.append(
            "动态照片由一张 JPG 和一段约 1.5 秒的 MP4 组成。很多系统把这段 MP4 "
            "排除在“图片”和“视频”之外，因此它会显示在“其他文件”里。"
        )
        lines.append("")
        rows = [
            [month, f"{count:,}", human(size)]
            for month, count, size in month_stats(data.live_pairs)
        ]
        lines.append(md_table(["年月", "数量", "体积"], rows))
        lines.append("")
        lines.append(
            f"**合计：{len(data.live_pairs):,} 个 / {human(live_size(data.live_pairs))}**"
        )
        lines.append("")
        if not data.live_duration_available:
            lines.append(
                "> 无法读取 MediaStore 时长，本次未做 5 秒过滤；数字仅用于展示，"
                "不建议据此执行删除。"
            )
            lines.append("")
        if data.live_long:
            lines.append(f"- 时长超过 5 秒、已排除：{len(data.live_long):,} 个 / {human(live_size(data.live_long))}")
        if data.live_unknown:
            lines.append(f"- 无法读取时长、默认不处理：{len(data.live_unknown):,} 个 / {human(live_size(data.live_unknown))}")
        if data.live_long or data.live_unknown:
            lines.append("")
        lines.append(
            "本工具**不会**在 `junk`/`dupes` 中删除这些文件。"
            "如果确认不需要动效，可先备份再执行 `adbclean live --delete --backup DIR`。"
        )
        lines.append("")

    if data.large_files:
        lines.append("## 大文件（Top 20，≥ 200 MB）")
        lines.append("")
        rows = [[path, human(size)] for path, size in data.large_files]
        lines.append(md_table(["文件", "体积"], rows))
        lines.append("")

    lines.append("## 安全声明")
    lines.append("")
    lines.append("- 全程不需要 Root，不修改系统分区，不执行 `pm clear`。")
    lines.append("- 默认只读；`--apply` / `--delete` 才可能删除，且删除前会要求确认。")
    lines.append("- 只处理当前用户（User 0）的 `/sdcard`，不触碰隐私空间、应用分身。")
    lines.append("- 不删除 `DCIM`、`Pictures`、`Movies`、`Documents`、`Download` 中的个人文件。")
    lines.append("- 每次删除都会在本机生成 TSV 清单，记录路径、体积和 MD5（如适用）。")
    lines.append("")
    return "\n".join(lines)


def render_html(data: ReportData) -> str:
    total = data.scan.total_size
    storage = data.storage
    total_bytes = storage.get("total_kb", 0) * 1024
    used_bytes = storage.get("used_kb", 0) * 1024
    avail_bytes = storage.get("avail_kb", 0) * 1024

    def card(title: str, value: str, hint: str = "") -> str:
        return (
            '<div class="card"><div class="card-title">'
            + html.escape(title)
            + '</div><div class="card-value">'
            + html.escape(value)
            + '</div><div class="card-hint">'
            + html.escape(hint)
            + "</div></div>"
        )

    parts: List[str] = []
    parts.append("<!doctype html>")
    parts.append('<html lang="zh-CN"><head><meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append("<title>安卓存储审计报告</title>")
    parts.append("<style>" + _CSS + "</style></head><body>")
    parts.append("<h1>安卓存储审计报告</h1>")
    parts.append(
        '<p class="meta">'
        + html.escape(_device_name(data))
        + " ｜ 序列号 "
        + html.escape(mask_serial(data.scan.serial))
        + " ｜ Android "
        + html.escape(data.device.get("ro.build.version.release", "?"))
        + " (API "
        + html.escape(data.device.get("ro.build.version.sdk", "?"))
        + ") ｜ "
        + html.escape(data.scan.scanned_at or data.generated_at)
        + "</p>"
    )
    parts.append(
        '<p class="meta">扫描范围 <code>'
        + html.escape(data.scan.root)
        + "</code> ｜ "
        + f"{data.scan.total_files:,} 个文件 ｜ "
        + html.escape(human(total))
        + " ｜ 十进制单位，与手机设置页一致</p>"
    )

    parts.append('<div class="cards">')
    parts.append(card("总容量", human(total_bytes)))
    parts.append(card("已用", human(used_bytes), percent(used_bytes, total_bytes)))
    parts.append(card("可用", human(avail_bytes)))
    parts.append(card("可清理缓存", human(data.junk_total), "白名单"))
    parts.append(card("动态照片伴生视频", human(live_size(data.live_pairs)), f"{len(data.live_pairs):,} 个"))
    parts.append("</div>")

    parts.append("<h2>文件分类</h2>")
    rows = []
    for key in CATEGORY_ORDER:
        if key not in data.categories:
            continue
        size, count = data.categories[key]
        rows.append([CATEGORY_LABELS[key], f"{count:,}", human(size), percent(size, total)])
    parts.append(_html_table(["分类", "文件数", "体积", "占比"], rows, numeric=(1, 2, 3)))
    parts.append('<p class="meta">视频分类包含动态照片伴生 MP4；详见下方“动态照片伴生视频”。</p>')

    parts.append("<h2>占用最大的目录（Top 20）</h2>")
    rows = [[path, human(size), percent(size, total)] for path, size in data.top_dirs if size > 0]
    parts.append(_html_table(["目录", "体积", "占比"], rows, numeric=(1, 2), code_cols=(0,)))

    parts.append("<h2>可安全清理项（白名单）</h2>")
    if data.junk:
        rows = [[path, f"{count:,}", human(size)] for path, size, count in data.junk]
        parts.append(_html_table(["目录", "文件数", "体积"], rows, numeric=(1, 2), code_cols=(0,)))
        parts.append(
            '<p class="warn">合计 <strong>'
            + html.escape(human(data.junk_total))
            + "</strong>。只包含缓存、缩略图、临时/日志目录；清理命令："
            + "<code>adbclean junk --apply</code>。</p>"
        )
    else:
        parts.append("<p>未发现白名单内的缓存/缩略图目录。</p>")

    if data.live_pairs or data.live_long or data.live_unknown:
        parts.append("<h2>动态照片伴生视频</h2>")
        parts.append(
            '<p class="warn">动态照片 = JPG + 短 MP4。很多系统把伴生 MP4 排除在'
            "“图片/视频”之外，所以它计入“其他文件”。本工具只统计，"
            "不会在 junk/dupes 中删除；需要时用 <code>adbclean live --delete --backup DIR</code>。</p>"
        )
        rows = [[month, f"{count:,}", human(size)] for month, count, size in month_stats(data.live_pairs)]
        parts.append(_html_table(["年月", "数量", "体积"], rows, numeric=(1, 2)))
        parts.append(
            "<p><strong>合计："
            + f"{len(data.live_pairs):,} 个 / {html.escape(human(live_size(data.live_pairs)))}"
            + "</strong></p>"
        )
        if not data.live_duration_available:
            parts.append(
                '<p class="warn">无法读取 MediaStore 时长，本次未做 5 秒过滤；'
                "数字仅用于展示，不建议据此执行删除。</p>"
            )
        if data.live_long:
            parts.append(
                "<p>时长超过 5 秒、已排除："
                + f"{len(data.live_long):,} 个 / {html.escape(human(live_size(data.live_long)))}</p>"
            )
        if data.live_unknown:
            parts.append(
                "<p>无法读取时长、默认不处理："
                + f"{len(data.live_unknown):,} 个 / {html.escape(human(live_size(data.live_unknown)))}</p>"
            )

    if data.large_files:
        parts.append("<h2>大文件（Top 20，≥ 200 MB）</h2>")
        rows = [[path, human(size)] for path, size in data.large_files]
        parts.append(_html_table(["文件", "体积"], rows, numeric=(1,), code_cols=(0,)))

    parts.append("<h2>安全声明</h2><ul>")
    for item in (
        "全程不需要 Root，不修改系统分区，不执行 pm clear。",
        "默认只读；--apply / --delete 才可能删除，且删除前会要求确认。",
        "只处理当前用户（User 0）的 /sdcard，不触碰隐私空间、应用分身。",
        "不删除 DCIM、Pictures、Movies、Documents、Download 中的个人文件。",
        "每次删除都会在本机生成 TSV 清单，记录路径、体积和 MD5（如适用）。",
    ):
        parts.append("<li>" + html.escape(item) + "</li>")
    parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _html_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    numeric: Sequence[int] = (),
    code_cols: Sequence[int] = (),
) -> str:
    parts = ['<table><thead><tr>']
    for index, header in enumerate(headers):
        cls = ' class="num"' if index in numeric else ""
        parts.append(f"<th{cls}>{html.escape(header)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        for index, cell in enumerate(row):
            cls = ' class="num"' if index in numeric else ""
            value = html.escape(str(cell))
            if index in code_cols:
                value = f"<code>{value}</code>"
            parts.append(f"<td{cls}>{value}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def write_reports(data: ReportData, out_dir: str, want_html: bool = True) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    base = f"report-{data.scan.serial}-{stamp()}"
    paths: List[str] = []
    markdown_path = os.path.join(out_dir, base + ".md")
    with open(markdown_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(data))
    paths.append(markdown_path)
    if want_html:
        html_path = os.path.join(out_dir, base + ".html")
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(render_html(data))
        paths.append(html_path)
    return paths


_CSS = """
:root{--bg:#ffffff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--soft:#f6f8fa;--accent:#0969da;--warnbg:#fff8c5;--warnline:#d4a72c}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--line:#30363d;--soft:#161b22;--accent:#58a6ff;--warnbg:#2d2412;--warnline:#9e6a03}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.6;max-width:1100px;margin:40px auto;padding:0 20px}
h1{font-size:28px;margin-bottom:6px}
h2{font-size:20px;margin-top:32px;border-bottom:1px solid var(--line);padding-bottom:6px}
.meta{color:var(--muted);font-size:13px;margin:4px 0}
code{background:var(--soft);border:1px solid var(--line);border-radius:4px;padding:1px 4px;font-size:12px;word-break:break-all}
table{border-collapse:collapse;width:100%;margin:12px 0 20px;font-size:13px}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:20px 0}
.card{border:1px solid var(--line);border-radius:10px;padding:14px;background:var(--soft)}
.card-title{color:var(--muted);font-size:12px}
.card-value{font-size:22px;font-weight:650;margin-top:4px}
.card-hint{color:var(--muted);font-size:12px;margin-top:2px}
.warn{background:var(--warnbg);border-left:4px solid var(--warnline);padding:10px 14px;border-radius:4px}
ul{padding-left:22px}
"""
