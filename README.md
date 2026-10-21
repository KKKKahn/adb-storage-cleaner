# adbclean

> **不 Root、纯 ADB 的安卓存储审计与安全清理工具。**
> 先看清楚空间去哪了，再决定删什么。

[![CI](https://github.com/KKKKahn/adb-storage-cleaner/actions/workflows/ci.yml/badge.svg)](https://github.com/KKKKahn/adb-storage-cleaner/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Android%208%2B-brightgreen)]()

`adbclean` 通过 USB 调试（ADB）读取手机存储，生成可读的报告，并且只清理**明确列入白名单**的缓存/缩略图/临时目录。默认全部是只读的；只有显式传入 `--apply` 或 `--delete` 才会删除，而且每次删除都会在电脑上留下 TSV 清单。

- 不需要 Root，不刷机，不修改系统分区，不执行 `pm clear`
- 不碰照片、视频、聊天记录、文档等个人文件
- 不扫描隐私空间（User 666）和应用分身（User 999）
- 删除前可预览、可确认、可追溯；动态照片支持先备份再删

> iPhone 不支持。iOS 没有 ADB 这类通用调试通道，清理方式完全不同。

---

## 目录

- [为什么做这个](#为什么做这个)
- [功能](#功能)
- [安全模型](#安全模型先读)
- [安装](#安装)
- [快速开始](#快速开始)
- [命令详解](#命令详解)
- [真实案例：82.67 GB 的“其他文件”](#真实案例8267-gb的其他文件)
- [兼容性](#兼容性)
- [常见问题](#常见问题)
- [开发](#开发)
- [English](#english)
- [免责声明](#免责声明)

---

## 为什么做这个

手机设置里的“其他”经常莫名其妙几十 GB，点进去只有几个 15 MB 的文件；清理软件扫一遍只告诉你“缓存 2 GB”。真正占空间的往往是：

- 应用在 `/sdcard/Android/data` 里的缓存、缩略图、离线资源、日志；
- 相册的 `.thumbnails`、隐藏目录、回收站；
- **动态照片的伴生 MP4**——系统既不把它算图片，也不算视频，最后塞进“其他文件”；
- 跨目录的完全重复文件（聊天应用保存、互传、办公套件、下载）。

这些都不需要 Root，用 ADB 就能看到。问题是手工 `du` + `find` 很容易误删。`adbclean` 把这次“审计 → 分类 → 白名单清理”的过程固化成一个可重复、可审查的工具。

## 功能

| 命令 | 作用 | 会删除文件吗 |
|---|---|---|
| `adbclean devices` | 列出已连接设备 | 否 |
| `adbclean info` | 设备、存储、缓存、多用户概览 | 否 |
| `adbclean scan` | 扫描 `/sdcard`，缓存完整文件清单 | 否 |
| `adbclean report` | 生成 Markdown + HTML 审计报告 | 否 |
| `adbclean junk` | 清理白名单缓存/缩略图目录 | 仅 `--apply` |
| `adbclean dupes` | 按 MD5 找出完全重复文件，保留主干目录 | 仅 `--apply` |
| `adbclean live` | 统计动态照片伴生 MP4，可备份后删除 | 仅 `--delete` |
| `adbclean apps` | 按应用统计 `/sdcard/Android/data` 占用 | 否 |
| `adbclean all` | 扫描 + 报告 + 清理建议 | 否 |

设计原则：**审计与删除分离；默认只读；白名单之外一律不动。**

## 安全模型（先读）

### 会做什么

- 用 `du -ab /sdcard/` 读取文件清单（只读）。
- 用 `md5sum` 在手机端计算候选文件的 MD5（只读）。
- 用 `content query` 读取 MediaStore 的视频时长（只读）。
- 删除时只执行 `rm -f` / `rm -rf`，目标必须通过白名单校验。

### 永远不会做什么

- 不执行 `pm clear`（会清空应用数据）。
- 不删除 `/sdcard/DCIM`、`Pictures`、`Movies`、`Music`、`Documents`、`Download` 里的个人文件。
- 不碰 `/sdcard/Android/obb`、`/sdcard/Android/media`。
- 不扫描、不删除 `/storage/emulated/666`（隐私空间）、`/999`（应用分身）或其他多用户目录。
- 不修改系统分区，不 Root，不刷机。
- 不上传任何数据；工具没有网络请求（除了 `adb` 本机进程）。

### 删除白名单

`junk` 只会删除以下目录（整个目录，不单独删文件）：

```text
/sdcard/.thumbnails
/sdcard/{DCIM,Pictures,Movies,Music,Download}/.thumbnails
/sdcard/Android/data/<包名>/cache
/sdcard/Android/data/<包名>/files/cache
/sdcard/Android/data/<包名>/**/<名字包含 cache/thumb/tmp/temp/log 的目录>
```

最后一条会匹配 `image_cache_new`、`thumbnails`、`logs`、`tmp`、`AVFSCache` 等，但**不会**匹配 `catalogs`、`photos`、`galleryOriginal`。

`dupes` 的保留优先级：

```text
DCIM > Pictures > Movies > Music/Documents > Download > 其他目录
     > Android/media > Android/data > vivoMediaData/.hide > 回收站
```

同优先级时：`互传`、`办公套件`、`聊天应用`、`备份`、`副本` 等目录降权；隐藏目录降权；缓存目录降权。

`Android/data` 里只有缓存类副本会自动删除；相机原片（如 Insta360 的 DNG/INSP）、数据库、应用媒体库，以及 `Android/obb`、`Android/media` 中的副本默认列为“需人工确认”，加 `--include-app-data` 才会处理。

### 可追溯

每次执行删除都会写入：

```text
~/.cache/adbclean/manifests/20260102-030405-junk-dirs.tsv
~/.cache/adbclean/manifests/20260102-030405-exact-duplicates.tsv
~/.cache/adbclean/manifests/20260102-030405-live-photo-mp4.tsv
```

macOS 对应 `~/Library/Caches/adbclean/manifests/`，Windows 对应 `%LOCALAPPDATA%\adbclean\manifests\`。

清单包含路径、体积，重复文件还包含 MD5 和保留副本的位置。

## 安装

要求：

- Python **3.9+**
- Android Platform Tools（`adb`）在 `PATH` 中
- 手机 Android **8.0+**（推荐 Android 10+），已开启“开发者选项 → USB 调试”
- 数据线（不是“仅充电”线）

```bash
# macOS
brew install android-platform-tools

# Ubuntu / Debian
sudo apt install android-tools-adb

# Windows：下载 platform-tools，解压后把目录加入 PATH
# https://developer.android.com/tools/releases/platform-tools
```

安装 `adbclean`：

```bash
pipx install git+https://github.com/KKKKahn/adb-storage-cleaner.git
# 或
git clone https://github.com/KKKKahn/adb-storage-cleaner.git
cd adb-storage-cleaner
python3 -m pip install -e .
```

没有 `pipx` 也可以直接用虚拟环境：

```bash
python3 -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

## 快速开始

```bash
# 1. 确认设备已授权
adb devices
adbclean devices

# 2. 看设备与存储概览
adbclean info

# 3. 扫描并生成报告（30–120 秒）
adbclean scan
adbclean report
open reports/report-*.html      # macOS
# Windows: start reports\report-*.html

# 4. 先看能清什么（dry-run，不删除）
adbclean junk

# 5. 确认后执行
adbclean junk --apply

# 6. 精确重复（dry-run，默认只处理 >= 1 MB）
adbclean dupes

# 7. 动态照片统计（只读）
adbclean live
```

### 多设备

```bash
adbclean devices
adbclean --serial 0123456789ABCDEF scan
# 或
adbclean scan --serial 0123456789ABCDEF
```

也可以设置 `ANDROID_SERIAL` 环境变量。

## 命令详解

### `scan`

```bash
adbclean scan
adbclean scan --root /sdcard/DCIM --timeout 600
```

扫描结果缓存在 `~/.cache/adbclean/scan-<序列号>.json.gz`。后续命令加 `--cached` 可复用，不必每次重扫。

> 扫描使用 `du -ab`（apparent size），可能和实际磁盘占用略有差异（压缩、稀疏文件、文件系统块对齐）。这是为了通用性做的取舍，见 [docs/DESIGN.md](docs/DESIGN.md)。

### `report`

```bash
adbclean report
adbclean report --cached --out ./reports --no-html
```

生成内容：

- 存储总览（总容量/已用/可用）
- 图片/视频/音频/文档/压缩包/安装包/其他 分类统计
- 占用最大的 20 个目录
- 白名单可清理项
- 动态照片伴生 MP4 按年月统计
- ≥ 200 MB 的大文件
- 安全声明

体积使用**十进制单位**（1 GB = 10⁹ 字节），和手机设置页一致；不是 GiB。

### `junk`

```bash
adbclean junk                 # dry-run
adbclean junk --apply         # 执行，删除前会要求确认
adbclean junk --apply --yes   # 非交互环境
```

只删白名单目录。缓存会被应用重新生成，所以释放量可能随后回落，这是正常的。

### `dupes`

```bash
adbclean dupes                        # dry-run
adbclean dupes --min-size 500K
adbclean dupes --apply                # 执行
adbclean dupes --apply --no-verify    # 跳过删除前的保留副本校验（不推荐）
```

流程：

1. 按字节大小分组（大小不同不可能完全相同）；
2. 设备端 `md5sum` 计算候选文件；
3. MD5 相同才视为重复；
4. 每组按保留优先级选一份保留；
5. 删除前重新校验保留副本的 MD5（默认开启）；
6. 删除其余副本，写清单。

哈希缓存绑定扫描时间戳：重新扫描后会重新计算，避免“同名同大小但内容已变”的误判。

### `live`

```bash
adbclean live                                   # 只统计
adbclean live --delete --backup ./live-backup   # 先备份，再删除 mp4
```

动态照片 = JPG + 约 1.5 秒的 MP4。工具只匹配：

```text
IMG_YYYYMMDD_HHMMSS.mp4  +  同名 .jpg
```

并额外要求：

- MediaStore 时长 ≤ `--max-duration`（默认 5000 ms）；
- 同名 JPG 必须存在；
- 排除 `Videoshot_*.mp4` 等录屏/缩略图命名；
- MediaStore 查不到时长的默认跳过（`--include-unknown` 可包含）。

删除只删 MP4，**JPG 永远保留**。备份目录会生成 `restore.sh`，可以一键推回：

```bash
cd live-backup && ./restore.sh
```

> 删除后照片还在，但长按/上滑不再有“动”的效果。请先备份或确认不需要动效。

### `apps`

```bash
adbclean apps
adbclean apps --cached
```

按包名统计 `/sdcard/Android/data` 与 `/obb` 的占用，并标注用户应用/系统应用。不含 `/data/data` 的内部数据（那部分需要 Root）。

## 真实案例：82.67 GB 的“其他文件”

以下数据来自一台 512 GB 的 vivo 真机（OriginOS 6 / Android 16），已做匿名化。

设置页显示：

| 分类 | 显示 |
|---|---:|
| 图片 | 95.63 GB |
| 视频 | 69.54 GB |
| 应用 | 136.11 GB |
| 系统 | 22.48 GB |
| **其他文件** | **82.67 GB** |
| **其他系统资源** | **31.55 GB** |
| 已用 / 总计 | 438.91 / 512 GB |

用 `adbclean scan` 看到的真实文件：

| 真实分类 | 体积 |
|---|---:|
| 图片文件 | 100.74 GB |
| 视频文件 | 158.55 GB |
| 其中：普通视频 | 79.35 GB |
| 其中：动态照片伴生 MP4 | **79.05 GB（14,477 个）** |
| 非媒体文件 | 6.85 GB |
| 动态照片 MP4（按 ≤5 秒过滤后） | 74.67 GB（13,738 个） |

关键结论：

1. **“其他文件 82.67 GB”里约 79 GB 是动态照片的伴生 MP4。**
   系统把它们排除在“图片”和“视频”之外，于是落进“其他”。它们不是垃圾，是照片“会动的那部分”。
2. **“其他系统资源 31.55 GB”主要是应用缓存、日志、数据库、缩略图和系统保留。**
   `dumpsys diskstats` 里的 App Cache 约 9.27 GB；剩余部分 ADB 无法完整归因，清理收益有限且会重建。
3. 设置页的“视频 69.54 GB”只统计普通视频；扫描到的视频文件共 158.55 GB，其中约 79.05 GB 是动态照片 MP4，普通视频约 79.35 GB，接近设置页的 69.54 GB（差异来自统计口径、缩略图和缓存）。
4. 这台机器通过白名单缓存清理释放了约 **15.3 GB**，通过 MD5 去重释放了约 **1.5 GB**；剩余最大的一块就是动态照片 MP4。

所以，如果你看到“其他”特别大，先跑：

```bash
adbclean live
```

看看到底是不是动态照片，再决定要不要为了空间放弃动效。**不要直接按“其他文件”乱删。**

## 兼容性

- Android 8.0+，推荐 10+。
- 已在 vivo 真机（Android 16 / OriginOS 6）验证。
- 其他厂商的注意事项见 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)。
- macOS / Linux / Windows 均可运行；Windows 的 `restore.sh` 建议用 Git Bash 或 WSL 执行。
- 不支持 iPhone / iPad / iOS。

## 常见问题

**Q：`adb devices` 显示 `unauthorized`？**
解锁手机，在“允许 USB 调试吗？”弹窗中勾选“始终允许”，再重试。锁屏状态下部分机型会断开 ADB。

**Q：显示多台设备？**
用 `--serial <序列号>` 指定，或设置 `ANDROID_SERIAL`。

**Q：扫描很慢 / 卡住？**
十万级文件通常 30–120 秒。保持手机解锁，避免省电模式杀 ADB。可以先用 `--root /sdcard/DCIM` 扫子目录试一下。

**Q：清理后空间没变？**
缓存会被应用立刻重建；部分文件系统统计有延迟。用 `adbclean info` 看 `df` 的实际变化。

**Q：为什么 `junk` 不删 `Android/data` 里的照片/原片？**
因为无法判断应用是否依赖它。工具只自动删缓存类目录；原片、数据库、媒体库一律列为“需人工确认”。

**Q：动态照片删了，相册里的照片会消失吗？**
不会。只删 MP4，JPG 保留。但照片不再有动效；相册可能需要重启或刷新才更新缩略图。

**Q：`dumpsys diskstats` 的数字和设置页不一致？**
部分 ROM 会缓存旧值（这台 vivo 的 Videos Size 就长期停在 168 GB）。以 `df` 和实际文件扫描为准。

**Q：会上传我的文件或路径吗？**
不会。工具没有网络代码；报告只写到本地。

**Q：iPhone 能用吗？**
不能。iOS 没有 ADB 这类通用调试接口，清理/备份需要用 Finder、iTunes 或 Apple Configurator，和本项目不是一个体系。

## 开发

```bash
git clone https://github.com/KKKKahn/adb-storage-cleaner.git
cd adb-storage-cleaner
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

pytest
ruff check src tests
```

项目结构：

```text
src/adbclean/
  adb.py        # adb 封装、df/diskstats/MediaStore
  scan.py       # du 扫描、缓存
  classify.py   # 分类、白名单、保留策略、安全护栏
  clean.py      # junk
  dupes.py      # MD5 去重
  live.py       # 动态照片
  apps.py       # 应用外部数据
  report.py     # Markdown / HTML
  cli.py        # 命令入口
tests/          # 纯函数单测，不需要连接手机
docs/           # 安全模型、设计、兼容性
```

新增白名单规则必须同时提交单元测试，并在 PR 描述里说明“为什么这个目录可以安全删除”。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## English

**adbclean** is a cross-platform CLI that audits and safely cleans Android storage over ADB — no root, no `pm clear`, no system modifications.

- `scan` / `report`: inventory `/sdcard`, classify files, generate Markdown + HTML reports.
- `junk`: delete only whitelisted cache/thumbnail/temp/log directories (dry-run by default).
- `dupes`: byte-identical duplicates via on-device MD5, keeping the copy in the main directory.
- `live`: identify live-photo companion MP4s (the usual cause of a huge “Other files” bucket), with optional backup and deletion while keeping every JPG.
- `apps`: per-package external storage usage.

Requirements: Python 3.9+, Android Platform Tools, Android 8+. iPhone is not supported.

Safety: read-only by default; destructive commands require `--apply`/`--delete`; every deletion is recorded in a local TSV manifest; privacy space / app clones (secondary users) are never touched.

```bash
pipx install git+https://github.com/KKKKahn/adb-storage-cleaner.git
adbclean report
adbclean junk          # dry-run
adbclean junk --apply  # explicit
```

## 免责声明

本项目按 MIT 协议开源，不提供任何担保。删除操作不可逆（除非你提前备份）；请先 dry-run、先备份、先在非重要数据上验证。因使用本工具造成的数据丢失，作者不承担责任。

与 vivo、OPPO、小米、华为、Google 等厂商无关联。
