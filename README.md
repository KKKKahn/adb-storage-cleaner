# Klean

> **不 Root 的安卓存储清理与实况照片压缩工具。**
> 电脑端走 ADB 审计与压缩，手机端可以完全离线运行。先看清空间去哪了，再决定删什么。

[![CI](https://github.com/KKKKahn/klean/actions/workflows/ci.yml/badge.svg)](https://github.com/KKKKahn/klean/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Android%208%2B-brightgreen)]()
[![macOS App](https://img.shields.io/badge/download-macOS%20App-4f6bff)](https://github.com/KKKKahn/klean/releases/tag/v0.5.2)
[![Android APK](https://img.shields.io/badge/download-Android%20APK-3ddc84)](https://github.com/KKKKahn/klean/releases/tag/android-v1.0.0)

`klean` 通过 USB 调试（ADB）读取手机存储，生成可读的报告，并且只清理**明确列入白名单**的缓存/缩略图/临时目录。默认全部是只读的；只有显式传入 `--apply` 或 `--delete` 才会删除，而且每次删除都会在电脑上留下 TSV 清单。

- 不需要 Root，不刷机，不修改系统分区，不执行 `pm clear`
- 不碰照片、视频、聊天记录、文档等个人文件
- 不扫描隐私空间（User 666）和应用分身（User 999）
- 删除前可预览、可确认、可追溯；动态照片支持先备份再删
- 实况照片可在浏览器里按日期/相册挑选，压缩伴生 MP4 后原子替换；JPG 定格画面永不修改

> iPhone 不支持。iOS 没有 ADB 这类通用调试通道，清理方式完全不同。

> **macOS 用户**：[下载 Klean 应用](https://github.com/KKKKahn/klean/releases/tag/v0.5.2) · Apple Silicon（M1–M6）· macOS 12+ · 内置 adb/ffmpeg，无需额外安装
>
> **Android 用户**：[下载 Klean APK](https://github.com/KKKKahn/klean/releases/tag/android-v1.0.0) · Android 10+ · 手机本地压缩、备份、替换和恢复，不需要电脑


---

## 目录

- [为什么做这个](#为什么做这个)
- [功能](#功能)
- [macOS 应用](#macos-应用)
- [Android 应用](#android-应用)
- [视频模式](#视频模式)
- [安全模型](#安全模型先读)
- [安装](#安装)
- [快速开始](#快速开始)
- [命令详解](#命令详解)
- [实况照片压缩工作台](#实况照片压缩工作台)
- [真实案例：82.67 GB 的“其他文件”](#真实案例8267-gb的其他文件)
- [兼容性](#兼容性)
- [常见问题](#常见问题)
- [开发](#开发)
- [English](#english)
- [免责声明](#免责声明)

---

## macOS 应用

[下载 macOS 应用](https://github.com/KKKKahn/klean/releases/tag/v0.5.2)

- 面向 Apple Silicon（M1–M6），macOS 12 Monterey 及以上。
- 内置 adb、ffmpeg、ffprobe 与 Python 运行时，不需要自己安装依赖。
- 打开 App → 按提示连接手机并开启 USB 调试 → 识别后自动进入工作台（原生窗口 + WKWebView）。
- 使用中拔线会提示断开，重新连接并解锁后自动恢复。
- App 为 ad-hoc 签名、未公证：第一次打开请右键 App →「打开」；或在「系统设置 → 隐私与安全性」里选择「仍要打开」。
- 所有处理都在本机完成，不上传文件。

从源码构建：

```bash
./packaging/macos/build.sh
# 输出 dist/klean-<version>-macos-arm64.dmg
```

构建脚本会下载 adb 与 ffmpeg；需要 Python 3.9+ 和网络。

## Android 应用

[下载 Klean Android APK](https://github.com/KKKKahn/klean/releases/tag/android-v1.0.0) · Android 10 及以上 · 无需 Root、无需电脑

Klean Android 把电脑端的核心流程完整搬到了手机上，而且不需要 ADB：

- **实况照片 + 普通视频** 两条独立赛道，自动识别同名的 JPG + MP4 配对，只处理伴生 MP4。
- 三列相册网格；长按后手指滑过多少张就选中多少张，滑到边缘会继续滚动。
- 压缩状态筛选（全部 / 未压缩 / 已压缩）、日期范围筛选、按时间 / 大小排序。
- 三档压缩质量；MediaCodec 硬件编码，实测实况短片段约 **2.7–7.1× 实时**，每张约 0.3–0.5 秒。
- 压缩完成后先进入结果页：逐条预览压缩版、查看节省比例和速度，勾选后才应用。
- 应用前自动把原 MP4 备份到 App 私有目录，原子替换、MD5 校验，JPG 定格画面永不修改。
- 保留 vivo 的 `vivoMediaExtInfo` uuid box；HDR / 10-bit 等不安全格式会跳过并说明原因。
- 顶部「备份」入口可以查看所有原片批次、预览、勾选恢复或删除；恢复后自动移除压缩历史。
- 全部在本机完成，App 不申请联网权限。

安装说明：

1. 下载 APK，允许浏览器/文件管理器「安装未知应用」；这是侧载应用，不上架应用商店。
2. 首次打开按提示授予「所有文件访问」权限；Android 10 会改为申请存储读写权限。
3. 备份位于 App 私有目录（`Android/data/com.kkkkahn.klean.mobile/files/backups`），**卸载 App 会一起删除备份**；确认手机上的压缩版正常前不要卸载。
4. 建议先选 1–3 张实况或一个小视频试压缩，预览并播放确认画质后再批量处理。

从源码构建 Android APK：

```bash
cd android
# 需要 JDK 17+、Android SDK 35
./gradlew assembleDebug
# 或用自己的签名构建 release：
KLEAN_ANDROID_KEYSTORE=/path/to/klean.jks \
KLEAN_ANDROID_STORE_PASSWORD=... \
KLEAN_ANDROID_KEY_ALIAS=klean \
KLEAN_ANDROID_KEY_PASSWORD=... \
./gradlew assembleRelease
```

APK 输出在 `android/app/build/outputs/apk/`。

## 为什么做这个

手机设置里的“其他”经常莫名其妙几十 GB，点进去只有几个 15 MB 的文件；清理软件扫一遍只告诉你“缓存 2 GB”。真正占空间的往往是：

- 应用在 `/sdcard/Android/data` 里的缓存、缩略图、离线资源、日志；
- 相册的 `.thumbnails`、隐藏目录、回收站；
- **动态照片的伴生 MP4**——系统既不把它算图片，也不算视频，最后塞进“其他文件”；
- 跨目录的完全重复文件（聊天应用保存、互传、办公套件、下载）。

这些都不需要 Root，用 ADB 就能看到。问题是手工 `du` + `find` 很容易误删。`klean` 把这次“审计 → 分类 → 白名单清理”的过程固化成一个可重复、可审查的工具。

## 功能

| 命令 | 作用 | 会删除文件吗 |
|---|---|---|
| `klean devices` | 列出已连接设备 | 否 |
| `klean info` | 设备、存储、缓存、多用户概览 | 否 |
| `klean scan` | 扫描 `/sdcard`，缓存完整文件清单 | 否 |
| `klean report` | 生成 Markdown + HTML 审计报告 | 否 |
| `klean junk` | 清理白名单缓存/缩略图目录 | 仅 `--apply` |
| `klean dupes` | 按 MD5 找出完全重复文件，保留主干目录 | 仅 `--apply` |
| `klean live` | 统计动态照片伴生 MP4，可备份后删除 | 仅 `--delete` |
| `klean studio` | 本地 Web 工作台：浏览、压缩、替换实况照片 MP4 | 仅点击「应用到手机」 |
| `klean apps` | 按应用统计 `/sdcard/Android/data` 占用 | 否 |
| `klean all` | 扫描 + 报告 + 清理建议 | 否 |

设计原则：**审计与删除分离；默认只读；白名单之外一律不动。**

## 安全模型（先读）

### 会做什么

- 用 `du -ab /sdcard/` 读取文件清单（只读）。
- 用 `md5sum` 在手机端计算候选文件的 MD5（只读）。
- 用 `content query` 读取 MediaStore 的视频时长（只读）。
- 删除时只执行 `rm -f` / `rm -rf`，目标必须通过白名单校验。
- 压缩工作台只会替换**已在扫描清单中确认过 JPG + MP4 配对**的伴生 MP4；替换前把原文件备份到电脑并校验 MD5。

### 永远不会做什么

- 不执行 `pm clear`（会清空应用数据）。
- 不删除或改写任何 JPG / JPEG；定格画面始终是原文件。
- 未备份不覆盖：默认每次替换都先在本机备份原 MP4，失败可恢复。
- 不删除 `/sdcard/DCIM`、`Pictures`、`Movies`、`Music`、`Documents`、`Download` 里的个人文件（`junk` 白名单之外）。
- 不碰 `/sdcard/Android/obb`、`/sdcard/Android/media`。
- 不扫描、不删除 `/storage/emulated/666`（隐私空间）、`/999`（应用分身）或其他多用户目录。
- 不修改系统分区，不 Root，不刷机。
- 不上传任何数据；本地工作台只监听 `127.0.0.1`，没有外部网络请求。

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
~/.cache/klean/manifests/20260102-030405-junk-dirs.tsv
~/.cache/klean/manifests/20260102-030405-exact-duplicates.tsv
~/.cache/klean/manifests/20260102-030405-live-photo-mp4.tsv
```

macOS 对应 `~/Library/Caches/klean/manifests/`，Windows 对应 `%LOCALAPPDATA%\klean\manifests\`。

清单包含路径、体积，重复文件还包含 MD5 和保留副本的位置。

## 安装

要求：

- Python **3.9+**
- Android Platform Tools（`adb`）在 `PATH` 中
- 手机 Android **8.0+**（推荐 Android 10+），已开启“开发者选项 → USB 调试”
- 数据线（不是“仅充电”线）
- 压缩功能需要 **ffmpeg / ffprobe**（推荐 7.x）

```bash
# macOS
brew install android-platform-tools ffmpeg

# Ubuntu / Debian
sudo apt install android-tools-adb ffmpeg

# Windows：下载 platform-tools，解压后把目录加入 PATH
# ffmpeg: https://www.gyan.dev/ffmpeg/builds/
# https://developer.android.com/tools/releases/platform-tools
```

安装 `klean`：

```bash
pipx install git+https://github.com/KKKKahn/klean.git
# 或
git clone https://github.com/KKKKahn/klean.git
cd klean
python3 -m pip install -e .
```

没有 `pipx` 也可以直接用虚拟环境：

```bash
python3 -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

## 视频模式

启动页先选择 **实况照片** 或 **普通视频**；两者是完全独立的赛道，列表、选择、任务、历史和备份互不影响。实况工作台在 `/studio`，视频工作台在 `/videos`。

- 仅处理普通 MP4（MediaStore `video/mp4`），自动排除实况伴生 MP4；短 `IMG_*.mp4` 残片会跳过。
- 视频缩略图、时长和分辨率直接来自手机 MediaStore，不需要把整段视频拉回电脑。
- 压缩 H.264 / HEVC 8-bit，保留分辨率；提供 CRF20（推荐）和 CRF23（小体积）两档。硬件收益不足时自动回退软件 CRF20/23，选择最小结果；已经紧凑到无法变小的小视频会跳过，不会写入更大的文件。
- 12 核以上芯片默认 6 路视频并发（其他芯片 3 路）；小于 1 MB 的紧凑小视频跳过软件重试以保持速度。
- HDR、10-bit、其他编码默认跳过并说明原因；原片预览暂不在电脑端做，压缩结果可预览。
- 视频使用独立历史与备份，备份优先硬链接；应用后可在任务面板恢复原视频，「原片备份」入口也能直接恢复视频批次。
- 与实况照片一样支持压缩状态筛选和日期范围压缩；批量选择只作用于当前筛选结果。
- 建议按总大小分批：默认提示每批 ≤ **6 GB**（上限 8 GB），不要按个数。视频默认保留备份，不提供常规的“跳过备份”选项。

## 平台支持

核心功能只依赖 Python、ADB 和 ffmpeg，不依赖 macOS 专有接口；只有“硬件加速”是 macOS 专有的。

| 平台 | 状态 | 硬件编码 | 说明 |
|---|---|---|---|
| macOS（Apple Silicon） | 已验证 | VideoToolbox | 自动并发（M6 级 12 核以上 6 路，其余 3 路） |
| 其他 Mac（Intel） | 可用 | VideoToolbox（ffmpeg 支持时） | 不支持时自动回退软件 |
| Windows 10/11 | 可用（未做 Windows 真机回归） | 无，使用软件 veryfast | 需要 vivo/Google USB 驱动、Python、adb、ffmpeg 在 PATH |
| Linux | 可用（软件编码） | 无 | 需要 adb 和 ffmpeg |

Windows 上的备份目录是 `%APPDATA%\klean\studio\backups`，任务缓存在 `%LOCALAPPDATA%\klean\studio\jobs`。网页里的「恢复原文件」可以直接用；批次里的 `restore.sh` 是 POSIX 脚本，手动执行需要 Git Bash 或 WSL。

Windows PowerShell 快速开始：

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
klean studio
# adb/ffmpeg 不在 PATH 时：
# klean studio --adb "C:\platform-tools\adb.exe" `
#   --ffmpeg "C:\ffmpeg\bin\ffmpeg.exe" --ffprobe "C:\ffmpeg\bin\ffprobe.exe"
```

Windows 没有 VideoToolbox，压缩会走软件 veryfast，速度和压缩率仍可用，但比 Mac 硬件编码慢；界面会显示“未检测到硬件编码”的提示。手机端仍需要安装对应厂商的 USB 驱动并授权 USB 调试。

## 快速开始

```bash
# 1. 确认设备已授权
adb devices
klean devices

# 2. 看设备与存储概览
klean info

# 3. 扫描并生成报告（30–120 秒）
klean scan
klean report
open reports/report-*.html      # macOS
# Windows: start reports\report-*.html

# 4. 先看能清什么（dry-run，不删除）
klean junk

# 5. 确认后执行
klean junk --apply

# 6. 精确重复（dry-run，默认只处理 >= 1 MB）
klean dupes

# 7. 动态照片统计（只读）
klean live

# 8. 打开实况照片压缩工作台
klean studio
```

### 多设备

```bash
klean devices
klean --serial 0123456789ABCDEF scan
# 或
klean scan --serial 0123456789ABCDEF
```

也可以设置 `ANDROID_SERIAL` 环境变量。

## 命令详解

### `scan`

```bash
klean scan
klean scan --root /sdcard/DCIM --timeout 600
```

扫描结果缓存在 `~/.cache/klean/scan-<序列号>.json.gz`。后续命令加 `--cached` 可复用，不必每次重扫。

> 扫描使用 `du -ab`（apparent size），可能和实际磁盘占用略有差异（压缩、稀疏文件、文件系统块对齐）。这是为了通用性做的取舍，见 [docs/DESIGN.md](docs/DESIGN.md)。

### `report`

```bash
klean report
klean report --cached --out ./reports --no-html
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
klean junk                 # dry-run
klean junk --apply         # 执行，删除前会要求确认
klean junk --apply --yes   # 非交互环境
```

只删白名单目录。缓存会被应用重新生成，所以释放量可能随后回落，这是正常的。

### `dupes`

```bash
klean dupes                        # dry-run
klean dupes --min-size 500K
klean dupes --apply                # 执行
klean dupes --apply --no-verify    # 跳过删除前的保留副本校验（不推荐）
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
klean live                                   # 只统计
klean live --delete --backup ./live-backup   # 先备份，再删除 mp4
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
klean apps
klean apps --cached
```

按包名统计 `/sdcard/Android/data` 与 `/obb` 的占用，并标注用户应用/系统应用。不含 `/data/data` 的内部数据（那部分需要 Root）。

## 实况照片压缩工作台

vivo 实况照片 = 同名 JPG（定格画面）+ 约 2 秒的 MP4（动态片段）。系统把伴生 MP4 排除在“图片”和“视频”之外，所以 大容量手机里几十 GB 会落到“其他文件”。

`klean studio` 在电脑上开一个本地网页，用来看图、选图、压缩 MP4，然后原子替换回手机。**JPG 永远不动。**

```bash
klean studio                 # 用上次扫描缓存，自动打开浏览器
klean studio --rescan        # 启动前重新扫描
klean studio --cached        # 强制使用缓存
klean studio --port 8765 --no-browser
klean studio --include-unknown
```

默认只监听 `127.0.0.1`，每次启动生成随机 token；网页没有外部 CDN 或网络请求。

### 界面功能

- 左侧 **按日期**（年/月/日倒序）和 **按相册**（实际文件夹）两种浏览方式。
- 打开即显示最近一天的缩略图；缩略图用 JPG 生成，按需拉取、本地缓存。
- 支持「全选本日」「全选本月」「全选本相册」，选择可跨日期、跨相册保留；整批选中后按钮会变成「取消全选」，再点一次即可取消本批。
- 「已选照片」视图集中复核，再统一压缩。
- 体积筛选（≥2/4/8/16 MB）、压缩状态筛选（全部 / 未压缩 / 已压缩，分段按钮直接可见）、按时间/体积排序、一键选最大的 50 个。批量选择只作用于当前筛选结果。
- 底部已选数量旁可一键「清空已选」，跨日期、跨筛选已勾选的内容也会一次清空。
- **日期范围压缩**：选择开始与结束日期后，打开区间内的全部内容，并自动勾选可压缩项；已压缩照片会说明跳过原因。普通视频页面同样支持。
- 每张卡片显示时间、MP4 大小、时长；双击卡片可直接播放手机上的实况 MP4；结果面板显示压缩前后大小、节省比例、SSIM（可选）、耗时，并可预览压缩版。
- 任务面板固定在内容区顶部：运行中吸顶显示实时进度，完成后保留，可继续应用或恢复，也可收起详情。
- 压缩过程中可点「停止任务」，当前文件结束后停止排队；已完成的结果仍可应用。

### 压缩档位

| 档位 | 参数 | 体积（实测样本） | 说明 |
|---|---|---:|---|
| 视觉无损 | H.264 CRF 18 | 56–59% | 最在意画质 |
| 推荐 | H.264 CRF 20 | 43–45% | 默认，SSIM ≈ 0.99 |
| 小体积 | H.264 CRF 23 | 29–30% | 建议先人眼确认 |
| 自定义 | CRF 14–30 | 可变 | 自己权衡 |
| 无损重封装 | `-c copy` | 几乎不变 | 像素完全不变，但不承诺减小体积 |

> CRF18 是“视觉无损”，不是数学无损。真正的无损只有 stream copy 重封装，而它几乎不省空间。

### 编码速度

macOS 上默认用 VideoToolbox 硬件编码；其他系统或 ffmpeg 不支持时回退到软件 veryfast。

| 速度 | 编码器 | 特点 |
|---|---|---|
| 硬件加速 | `h264_videotoolbox` | 最快；档位映射到 `-q:v` 50/60/65 |
| 快 | libx264 veryfast | 兼容性优先，速度约为 slow 的 3–4 倍 |
| 均衡 | libx264 medium | 体积更小一点 |
| 最小体积 | libx264 slow | 最慢，压缩率最高 |

SSIM 默认关闭（计算需要额外解码两段视频）；解码完整性校验仍会执行。压缩默认自动选择并发：Apple Silicon 硬件编码且 CPU ≥ 12 核时 6 路，其余 3 路；应用默认 4 路并发，可在 `klean studio --workers 6 --apply-workers 4` 调整。

在 M6（12 核）真机 24 张样本上，6 路硬件编码比 3 路快约 23%（4.44s → 3.40s）；8 路没有进一步收益。硬件解码、`-realtime`、`-prio_speed` 实测均未提速，因此保持当前路径。

支持所有宽高比：不缩放、不改变裁剪比例；H.264 level 按宏块/帧率计算，4:3、1:1、竖屏、超宽裁剪都能处理。奇数宽高只会裁掉最多 1 像素。VideoToolbox 初始化失败会自动回退软件编码。硬件编码节省不足 10% 时，会自动改用软件 x264 同档 CRF（推荐档为 CRF20）；仍不足再试 CRF23，最后才跳过。源文件码率很高时，这个回退通常能把“只省 2–6%”提升到“省 35–50%”。

两段真机样本（1920×1080，H.264，P3，full-range，已匿名化）：

| 样本 | 节省 | SSIM |
|---|---:|---:|
| 样本 A（横屏） | 57.4% | 0.9920 |
| 样本 B（竖屏） | 55.5% | 0.9888 |

### 为什么需要保留 vivo uuid box

MP4 末尾有一个顶层 `uuid` box（`vivoMediaExtInfo`），里面包含 livephoto ID。ffmpeg 重编码会丢掉它，相册就不再识别为实况照片。工具会从原 MP4 原样提取这个 box，追加到压缩文件末尾，再校验文件可解码。

流程（每个文件）：

1. 校验文件在扫描得到的 JPG + MP4 配对清单中；
2. 拉取 MP4 到电脑，校验大小与 MD5；
3. ffprobe 检查时长、编码、像素格式、色彩、旋转；
4. 重编码 + 追加 uuid + 解码校验 + SSIM；
5. 替换前再次读取手机 MD5，确认文件没被改动；
6. 备份原 MP4 到电脑；
7. push 临时文件 → 手机端 MD5 → `chmod 660` → 恢复 mtime → `mv -f` 原子替换；
8. 再次 MD5 校验 → 媒体扫描 → 更新本地清单。

以下情况默认**跳过**，不会冒险压缩：

- HDR / 10-bit / BT.2020；
- 非 `yuv420p` / `yuvj420p` 像素格式；
- 时长超过 `--max-duration`（默认 5000 ms）；
- 找不到同名 JPG；
- 压缩后没有变小；
- 原 MP4 没有 `vivoMediaExtInfo` uuid box；
- 手机文件在压缩期间发生变化。

### 分批与已压缩记录

- 不再限制单次 500 个。界面建议每批约 **200 个**（或 10–15 GB），大批量仍可执行。
- 空间不足时不会整批拒绝：先处理能容纳的文件，剩余标记为空间不足；应用后再执行即可继续。
- 每次「应用到手机」都会写入本机历史：`~/Library/Application Support/klean/studio/history.json`（Windows: `%APPDATA%\klean\studio\history.json`）。
- 历史匹配时，照片卡片右上角显示小勾图标（悬停可看 CRF/体积）；全选默认跳过这些照片。
- 需要再次压缩时，勾选「允许更深压缩」；只有更高 CRF / 更小体积档位才会执行，否则会在结果里说明并跳过。
- 恢复原图会自动移除对应历史，之后可以重新正常压缩。
- 历史只记录本工具成功应用的文件；手机文件大小变化（例如恢复或被外部修改）后，历史会自动失效。

### 备份与恢复

备份放在电脑上，不占手机空间：

```text
# macOS
~/Library/Application Support/klean/studio/backups/20260103-040506/
  sdcard/DCIM/Camera/IMG_20260103_040506.mp4
  manifest.tsv
  restore.sh
```

- 普通视频原片备份在相邻的 `video-backups/`，结构相同。
- 每次「应用到手机」都会新建一个批次目录；`manifest.tsv` 记录原始大小、压缩大小、MD5、mtime。
- 备份优先使用**硬链接**（与任务源文件共用数据块），通常不产生额外写入。
- 应用成功后会删除任务目录里的源文件和压缩输出，只保留备份。
- 点击顶部的「备份管理」可以统一查看实况与视频的所有批次：缩略图来自本机备份，不依赖手机在线；可点开预览原片、**批量恢复**或按文件勾选清除。Studio 重启后仍然可用。
- 恢复时逐文件校验：手机文件必须仍是我们替换后的压缩版，或被外部改过就跳过，避免覆盖新内容。
- 「清除所选」只删除勾选的原片，并同步裁剪同批次的 `manifest.tsv` 与 `restore.sh`；也可删除单个批次或全部。删除不可逆，界面会二次确认。
- 每个批次也有 `restore.sh`，可以脱离 Studio 手动执行：

```bash
cd ~/Library/Application\ Support/klean/studio/backups/20260103-040506
./restore.sh
```

### 空间规划

本机需要同时容纳「拉取的源文件 + 压缩输出」，估算约为所选 MP4 总体积的 **1.5 倍 + 256 MB**。工作台会在开始前检查，空间不足直接拒绝；每处理一个文件也会再检查一次。

备份大约等于原始 MP4 的总体积（硬链接不额外占用）。例如本机剩 27 GB，建议每批选择 10–15 GB 原始 MP4，压缩并应用后再处理下一批。

macOS 上备份位于 `Application Support`，不会被系统当缓存清理；`~/Library/Caches/klean/studio/jobs` 里的任务临时文件可以随时删除。

更细的参数、验证方法和限制见 [docs/COMPRESSION.md](docs/COMPRESSION.md)。

## 真实案例：82.67 GB 的“其他文件”

以下数据来自一台大容量 vivo 手机（OriginOS 6 / Android 16），已做匿名化。

设置页显示：

| 分类 | 显示 |
|---|---:|
| 图片 | 95.63 GB |
| 视频 | 69.54 GB |
| 应用 | 136.11 GB |
| 系统 | 22.48 GB |
| **其他文件** | **82.67 GB** |
| **其他系统资源** | **31.55 GB** |
| 已用占比 | 约 86% |

用 `klean scan` 看到的真实文件：

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
klean live
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
缓存会被应用立刻重建；部分文件系统统计有延迟。用 `klean info` 看 `df` 的实际变化。

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
git clone https://github.com/KKKKahn/klean.git
cd klean
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

pytest
ruff check src tests
```

项目结构：

```text
src/klean/
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

**Klean** is a cross-platform CLI that audits and safely cleans Android storage over ADB — no root, no `pm clear`, no system modifications.

- `scan` / `report`: inventory `/sdcard`, classify files, generate Markdown + HTML reports.
- `junk`: delete only whitelisted cache/thumbnail/temp/log directories (dry-run by default).
- `dupes`: byte-identical duplicates via on-device MD5, keeping the copy in the main directory.
- `live`: identify live-photo companion MP4s (the usual cause of a huge “Other files” bucket), with optional backup and deletion while keeping every JPG.
- `apps`: per-package external storage usage.

Requirements: Python 3.9+, Android Platform Tools, Android 8+. iPhone is not supported.

Safety: read-only by default; destructive commands require `--apply`/`--delete`; every deletion is recorded in a local TSV manifest; privacy space / app clones (secondary users) are never touched.

```bash
pipx install git+https://github.com/KKKKahn/klean.git
klean report
klean junk          # dry-run
klean junk --apply  # explicit
```

## 免责声明

本项目按 MIT 协议开源，不提供任何担保。删除操作不可逆（除非你提前备份）；请先 dry-run、先备份、先在非重要数据上验证。因使用本工具造成的数据丢失，作者不承担责任。

与 vivo、OPPO、小米、华为、Google 等厂商无关联。
