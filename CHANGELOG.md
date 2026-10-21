# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 和语义化版本。

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
