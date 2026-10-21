# 实况照片 MP4 压缩

这份文档说明 `adbclean studio` 的压缩原理、参数、验证结果和安全边界。

## 适用对象

vivo / iQOO 的实况照片由两个文件组成：

```text
IMG_YYYYMMDD_HHMMSS.jpg   # 定格画面，4096×2304 左右；永远不动
IMG_YYYYMMDD_HHMMSS.mp4   # 约 2 秒动态片段，1920×1080；可压缩后替换
```

两者通过 vivo 私有 **livephoto ID** 配对：

- JPG 尾部有 `vivo{... "com.android.camera.livephoto":"..." ...}` 和 `cameralbum!` 附加数据；
- MP4 末尾有一个顶层 `uuid` box，user type 为 `vivoMediaExtInfo`，payload 里是同一个 ID；
- 实测 box 大小 359 B 或 493 B，**因机型/拍摄参数而异**，必须整段原样复制。

ffmpeg 重编码不会保留这个 uuid box。丢失后，相册可能仍显示 JPG，但不再识别为实况照片。

## 安全原则

1. **JPG 永不修改。** 工作台只读 JPG 生成缩略图，不写入、不重命名、不删除。
2. **只处理扫描确认过的配对。** MP4 必须同时满足：命名匹配、同名 JPG 存在、MediaStore 时长 ≤ 5 秒。
3. **替换前必须备份。** 原 MP4 备份到电脑，校验 MD5 后才开始替换。
4. **原子替换。** push 到同目录隐藏临时文件 → 手机端 MD5 → `chmod 660` → 恢复 mtime → `mv -f`。
5. **失败可回滚。** `mv` 后任何校验失败，立即从备份恢复；恢复也失败时停止并保留现场。
6. **不确定就跳过。** HDR、10-bit、BT.2020、异常像素格式、无 uuid、压缩无收益，全部不处理。

## 工作流

```text
扫描缓存 ──> JPG + MP4 配对 ──> 选择照片
                              │
                              ▼
                       拉取 MP4 到电脑
                              │
                              ▼
             ffprobe 检查 ──> ffmpeg 重编码
                              │
                              ▼
                  追加 vivo uuid + 解码校验 + SSIM
                              │
                              ▼
                     预览 ──> 应用到手机
                              │
                     ┌────────┴────────┐
                     ▼                 ▼
                备份原 MP4         原子替换 + 媒体扫描
                     │
                     ▼
              恢复 / 清除备份
```

## 压缩档位

| 档位 | 参数 | 实测体积 | 说明 |
|---|---|---:|---|
| 视觉无损 | libx264 CRF 18，preset slow | 56–59% | SSIM ≈ 0.993 |
| 推荐 | libx264 CRF 20，preset slow | 43–45% | 默认，SSIM ≈ 0.989–0.992 |
| 小体积 | libx264 CRF 23，preset slow | 29–30% | SSIM ≈ 0.986–0.990 |
| 自定义 | CRF 14–30 | 可变 | 界面限制范围，后端接受 1–51 |
| 无损重封装 | `-c copy` | 几乎不变 | 像素完全不变，不承诺减小 |

> “视觉无损”是观感意义上的，不是数学无损。真正的无损只有 stream copy；原片已经是压缩过的 H.264，重新无损编码通常会变大。

### 编码速度与硬件加速

macOS 上优先使用 `h264_videotoolbox`（VideoToolbox）。它不支持 CRF，代码把档位映射到 `-q:v`：

| 档位 | 软件 CRF | 硬件 `-q:v` |
|---|---:|---:|
| 视觉无损 | 18 | 65 |
| 推荐 | 20 | 60 |
| 小体积 | 23 | 50 |
| 自定义 ≥25 | 25–30 | 45 |

本地 4 文件基准（两段真机样本各两份，推荐档）：

| 模式 | 总耗时 | 每文件 | 输出总量 |
|---|---:|---:|---:|
| slow + SSIM（旧默认） | 31.38 s | 7.84 s | 7.06 MB |
| veryfast + 跳过 SSIM | 9.13 s | 2.28 s | 6.65 MB |
| veryfast + 跳过 SSIM + 2 路 | 6.71 s | 1.68 s | 6.65 MB |
| 硬件 + 跳过 SSIM + 3 路 | 4.19 s | 0.52 s | 约 6.5 MB |

硬件输出保留了 58/57 帧和逐帧 PTS；真机 8.50 MB 样本压到 4.79 MB，SSIM 约 0.989，相册动态和颜色确认正常。

VideoToolbox 的并发会话是软限制；超过 3–4 路后单路性能会下降，因此默认压缩 3 路、应用 4 路。硬件模式只在 macOS 上启用，其他平台回退到 veryfast。

SSIM 默认关闭（额外解码两段视频），但完整的解码校验保留。压缩任务可以中途停止，最多等当前 1–3 个文件结束。

两段真机样本的结果：

| 样本 | 原始 | CRF20 + uuid | 节省 | SSIM | 方向 |
|---|---:|---:|---:|---:|---|
| IMG_20260102_030406 | 3,425,268 B | 1,458,780 B | 57.4% | 0.9920 | 横屏 |
| IMG_20260102_030405 | 4,645,300 B | 2,068,829 B | 55.5% | 0.9888 | 竖屏（旋转 -90°） |

这两个输出与真机验证通过的文件 MD5 完全一致：

```text
sample-a: REDACTED
sample-b: REDACTED
```

## ffmpeg 参数

核心命令（实际由 `src/adbclean/compress.py` 生成）：

```bash
ffmpeg -hide_banner -loglevel error -y \
  -i input.mp4 \
  -map 0 \
  -c:v libx264 -preset slow -crf 20 \
  -pix_fmt yuvj420p \
  -profile:v high -level 4.0 \
  -color_range pc -colorspace bt470bg \
  -color_primaries smpte432 -color_trc iec61966-2-1 \
  -c:a copy -dn -sn -map_metadata 0 \
  -metadata com.android.version=16 \
  -metadata:s:v:0 handler_name=VideoHandle \
  -metadata:s:a:0 handler_name=SoundHandle \
  -vsync 0 -enc_time_base 1/90000 \
  -brand mp42 -movflags +faststart+use_metadata_tags \
  output.mp4
```

关键点：

| 参数 | 原因 |
|---|---|
| `-map 0` | 保持音频/视频 track 顺序（vivo 原文件音频 track 在前） |
| `-pix_fmt yuvj420p` + `-color_range pc` | 原片是 full-range，保持亮度和色阶 |
| `-colorspace bt470bg` / `smpte432` / `iec61966-2-1` | 分别对应原片 VUI 的矩阵、Display P3 原色、sRGB 传输 |
| `-c:a copy` | HE-AAC 原样复制；失败才回退 AAC 96k，并在结果里标注 |
| `-vsync 0` + `-enc_time_base 1/90000` | **必须保留 VFR 时间戳**；省略会导致帧对齐错位、SSIM 假性下降 |
| `-level 4.0` | 仅 1080p 使用；4K 省略，让 x264 自选 |
| `-movflags +faststart` | moov 前移，播放器打开更快 |
| `+use_metadata_tags` | 写入 `com.android.version` 等自定义 md4 |
| 追加 uuid | vivo 相册识别实况照片的关键 |

时间基不是固定值：代码优先使用 ffprobe 读到的 `time_base`，只有异常时才回退到 `1/90000`。

## 任意宽高比与裁剪

不缩放、不强制 16:9。输出的宽高比与源文件一致，支持横屏、竖屏、4:3、1:1、超宽和裁剪后的尺寸。

H.264 level 不是按“最长边”判断，而是按宏块数/秒计算：

```text
frame_mbs = ceil(width/16) × ceil(height/16)
mbps = frame_mbs × fps
level 4.0 要求 frame_mbs ≤ 8192 且 mbps ≤ 245760
```

例如 `1728×1296@30fps` 的 frame_mbs 是 8748，超过 level 4.0，代码会省略 `-level`，让编码器自动选择合适等级。之前强制所有 ≤1920 边长的视频用 level 4.0，正是 VideoToolbox 报 `-12902` 的原因。

裁剪后的奇数宽高无法直接用 4:2:0，代码只裁掉最多 1 像素（`crop=trunc(iw/2)*2:trunc(ih/2)*2`），不缩放、不拉伸；结果里会注明“奇数分辨率已裁掉 1 像素”。

### 自动回退

- VideoToolbox 初始化失败（例如 `-12902`）→ 自动改用软件 veryfast 重试，不把文件标记为错误。
- 硬件编码节省不足 10% → 自动改用软件 x264 同档 CRF；推荐档（CRF20）仍不足 10% 时再试 CRF23。选择体积最小的可用结果。
- 结果面板会显示“已回退软件”或“已改用软件 CRF20/CRF23”，不会静默改变质量。
- 高码率短视频（例如 24–32 Mbps、0.8–2.4 秒）常见硬件 q60 只省 2–6%；软件 CRF20/23 实测可省 35–50%，但会增加一次编码时间，仅对这些低收益文件触发。

## 旋转与色彩

- 竖屏视频可能以 1920×1080 + display matrix -90° 存储。ffmpeg 默认 autorotate，输出 1080×1920 且不带旋转矩阵。
- 已在真机验证：这种输出相册播放正常。
- 色彩 metadata 只接受白名单值；读到异常值时忽略该参数，而不是把未知字符串传给 ffmpeg。

## 跳过条件

| 条件 | 原因 |
|---|---|
| `bit_depth > 8` | 10/12-bit 可能是 HDR，8-bit 输出会偏色 |
| `color_transfer` 为 `smpte2084` / `arib-std-b67` | HDR |
| `color_primaries` 为 `bt2020` | 宽色域，暂不处理 |
| `pix_fmt` 不是 `yuv420p` / `yuvj420p` | 避免错误转换色度采样 |
| 时长 > `--max-duration` | 不是实况片段 |
| 没有同名 JPG | 可能不是实况照片，或 JPG 已被移动 |
| 没有 `vivoMediaExtInfo` uuid | 无法保证相册识别 |
| 输出不小于输入 | 没有收益 |
| 手机文件在压缩期间变化 | 拉取的源已过期 |
| ffmpeg 编码/解码/SSIM 失败 | 不冒险替换 |

## 备份与恢复

备份位置（持久化，不在 Caches 里）：

```text
macOS:   ~/Library/Application Support/adbclean/studio/backups/
Linux:   ${XDG_DATA_HOME:-~/.local/share}/adbclean/studio/backups/
Windows: %APPDATA%\adbclean\studio\backups\
```

每个「应用」批次一个目录：

```text
20260103-040506/
  sdcard/DCIM/Camera/IMG_20260103_040506.mp4
  manifest.tsv
  restore.sh
```

`manifest.tsv`：

```text
mp4  jpg  original_size  new_size  original_md5  new_md5  mtime
```

恢复逻辑：

1. 只接受备份目录内的批次名，拒绝路径穿越；
2. 校验备份文件 MD5 == `original_md5`；
3. 读取手机当前 MD5：
   - 等于 `new_md5`：正常恢复；
   - 等于 `original_md5`：已经是原文件，跳过写入；
   - 其他值：手机文件被外部修改，**跳过，不覆盖**；
4. push 临时文件 → MD5 → chmod/mtime → `mv -f` → 媒体扫描。

Studio 重启后任务列表会清空，但备份批次仍然可在顶部「本地备份」里恢复。也可以直接运行批次里的 `restore.sh`。

### 清除备份

- 应用完成后，结果面板提供「清除本批备份」。
- 备份列表里可以删除单个批次，或清除全部备份。
- 删除是不可逆的；删除后无法再恢复原始 MP4。界面会要求二次确认。
- 建议先逐张确认相册动态效果正常，再清除对应批次的备份。

## 空间规划

压缩一批文件时，本机需要：

```text
拉取的源 MP4（≈ 原始体积）
+ 压缩输出（≈ 原始体积 × 0.44）
+ 256 MB 余量
```

工作台按 `1.5 × 所选体积 + 256 MB` 估算，开始前和每个文件处理前都会检查。

备份使用**硬链接**（与任务源文件共用数据块），通常不额外占用空间。应用成功后会删除任务里的源文件和压缩输出，只保留备份。

示例：本机剩 27 GB，建议每批选择 10–15 GB 原始 MP4；完成并应用后再处理下一批。备份本身约占原始体积的 1 倍，需要给恢复留出空间。

## 架构

```text
src/adbclean/compress.py   # ffprobe、编码命令、SSIM、uuid 提取/追加
src/adbclean/studio.py     # HTTP 服务、任务、缩略图、备份、应用、恢复
src/adbclean/web/          # 原生 HTML/CSS/JS，无外部依赖
src/adbclean/adb.py        # push / md5 / mtime / media scan
src/adbclean/live.py       # JPG + MP4 配对
```

本地服务只监听 `127.0.0.1`，每次启动生成随机 token；所有 `/api/*` 校验 token，POST 还校验 Host/Origin。

## 验证方法

```bash
# 1. 单元测试
.venv/bin/pytest -q
.venv/bin/ruff check src tests

# 2. 单文件压缩（本地样本）
PYTHONPATH=src .venv/bin/python - <<'PY'
from adbclean.compress import PRESETS, compress_file
r = compress_file("in.mp4", "out.mp4", PRESETS["balanced"])
print(r)
PY

# 3. 检查 uuid 与元数据
ffprobe -v error -show_streams -show_format -of json out.mp4
strings -a out.mp4 | grep livephoto

# 4. 检查可解码
ffmpeg -v error -i out.mp4 -f null -
```

真机验收：

1. `adbclean studio`；
2. 选一张照片，CRF20，压缩；
3. 预览压缩版；
4. 应用，检查手机 MD5、MediaStore size/duration、mtime；
5. 在相册确认动态效果；
6. 点「恢复原文件」，确认 MD5 回到备份值；
7. 再压缩替换回去（可选）。

## 已知限制

- 只在 vivo 真机 / OriginOS 6 / Android 16 上完成真机验证。
- 其他厂商的实况照片（小米、OPPO、三星等）机制不同，默认不识别。
- HEVC、HDR、10-bit、4K 尚未完整验证；当前策略是跳过。
- SSIM 只是参考，运动、暗部、噪点仍需人眼确认。
- 备份不是云备份；电脑磁盘损坏/误删缓存目录仍会丢失。重要照片建议额外备份到外置磁盘或 NAS。
- 不支持 iOS。
