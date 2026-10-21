# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 和语义化版本。

## [0.2.1] - 2026-10-21

### Added

- macOS 硬件编码（VideoToolbox `h264_videotoolbox`），默认优先启用；不可用时自动回退到软件编码。
- 编码速度选择：硬件加速、快（veryfast）、均衡（medium）、最小体积（slow）。
- SSIM 改为可选（默认关闭）；解码完整性校验仍然保留。
- 压缩 3 路并发、应用 4 路并发（可调）。
- 压缩任务支持「停止任务」：当前文件结束后不再开始新文件，已完成部分仍可应用。
- 合并 ADB 往返：文件信息、MD5、权限、mtime、原子替换和媒体扫描尽量一次 shell 完成。

### Performance

- 本地 4 文件基准（CRF20）：旧默认 slow + SSIM 约 7.84 秒/个；硬件编码 + 跳过 SSIM + 3 路并发约 0.52 秒/个。
- 真机 8.50 MB 样本（硬件编码）：拉取、压缩、校验、替换合计约 1.3 秒，输出 4.79 MB，省 43.7%。
- 1000 个文件的耗时取决于文件大小和 USB 带宽，通常比旧默认快数倍；建议按月份/相册分批。

### Known limitations

- 硬件编码仅 macOS VideoToolbox 可用；输出 `color_space` 可能标记为 smpte170m（与 bt470bg 同为 601 矩阵），已在测试样本上确认颜色正常。
- 硬件编码不支持 CRF，按档位映射到 `-q:v` 50/60/65；软件编码仍使用 CRF。
- 停止任务不会立刻终止正在编码的 1–3 个文件，只停止后续排队文件。

## [0.2.0] - 2026-10-21

### Added

- `studio`：本地 Web 工作台，按日期 / 月份 / 相册浏览实况照片，缩略图、多选、体积筛选、排序、一键选最大的 50 个。
- 压缩引擎：视觉无损 CRF18、推荐 CRF20、小体积 CRF23、自定义 CRF、无损重封装。
- 保留 vivo `vivoMediaExtInfo` uuid box，ffmpeg 重编码后原字节追加；JPG 从不修改。
- 应用流程：备份原 MP4 → 推送临时文件 → 手机端 MD5 → chmod/mtime → 原子 `mv` → 媒体扫描。
- 持久化备份目录：`~/Library/Application Support/adbclean/studio/backups/`（macOS），每批含 `manifest.tsv` 和 `restore.sh`。
- 跨重启批量恢复：Studio 重启后仍可从备份列表按批次恢复；逐文件 MD5 校验，手机文件被外部修改时跳过。
- 清除备份：应用完成后可清除本批备份，备份列表支持删除单个批次或全部；二次确认，删除不可逆。
- 本地空间守卫：按选择体积估算所需空间，不足时拒绝开始；备份优先使用硬链接，应用成功后清理任务源文件和输出。
- 界面：重做桌面/窄屏布局，压缩栏不再遮挡内容；骨架屏、缩略图淡入、无限滚动、体积筛选/排序、已选视图、相册视图。
- 稳定性：视图请求竞态保护、按钮防重复提交、选择徽标原地更新、备份列表懒加载。
- 安全：仅监听 loopback、随机 token、Host/Origin 校验、路径白名单、请求体上限。
- 新增 `docs/COMPRESSION.md`；单元测试增至 78 个，覆盖压缩、uuid、备份目录、路径安全与 HTTP API。

### Changed

- 版本号升至 0.2.0；`pyproject.toml` 打包 `adbclean/web/*`。
- 动态照片编号与时长过滤沿用 `live` 的规则，Studio 只展示验证过的配对。

### Known limitations

- 仅在 vivo 真机 / OriginOS 6 / Android 16 上完成真机验证。
- HDR、10-bit、非 yuv420p/yuvj420p、4K 默认跳过；其他厂商的实况照片机制未验证。
- 本地备份需要与待处理体积相当的磁盘空间，建议按批次操作。
- Studio 的任务列表在重启后清空，但备份和 `restore.sh` 保留。

## [0.1.0] - 2026-10-21

### Added

- `devices`：列出已连接设备、状态、型号。
- `info`：设备信息、`/data` 存储总览、`dumpsys diskstats` 摘要、多用户检测。
- `scan`：通过 `du -ab /sdcard/` 扫描文件清单，缓存为 gzip JSON。
- `report`：生成 Markdown + HTML 存储审计报告（分类、Top 目录、白名单缓存、动态照片、大文件）。
- `junk`：白名单缓存/缩略图/临时目录清理，默认 dry-run，`--apply` 才执行。
- `dupes`：按大小分组 + 设备端 MD5 的精确去重，保留策略、删除前校验、TSV 清单。
- `live`：识别动态照片伴生 MP4，按时长过滤，支持 `--backup` 备份和 `--delete` 删除（JPG 永远保留）。
- `apps`：按包名统计 `/sdcard/Android/data` 和 `/obb` 占用。
- 安全护栏：只处理 User 0、拒绝 `..`、白名单校验、删除清单、10 进制体积单位。
- 32 个纯函数单元测试；GitHub Actions CI（Python 3.9–3.12 + macOS/Windows）。
- 文档：`README.md`、`docs/SAFETY.md`、`docs/DESIGN.md`、`docs/COMPATIBILITY.md`。

### Known limitations

- `du` 统计 apparent size，和 `df` 的块占用可能不同。
- 文件名含换行/反斜杠时，MD5 阶段会跳过。
- 部分 OEM 限制 `/sdcard/Android/data` 的 shell 访问。
- 不支持 iOS。
