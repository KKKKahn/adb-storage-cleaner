# 兼容性

## 已验证

| 设备 | 系统 | 结果 |
|---|---|---|
| vivo 真机 (TESTDEVICE) | Android 16 / OriginOS 6 | 扫描、报告、junk dry-run、dupes、live、apps、studio 压缩/替换/恢复均正常 |

### 实况照片压缩验证

- 横向样本（样本 A）：CRF20 后 3,425,268 → 1,458,780 B，SSIM 0.9920，相册动态正常。
- 竖向样本（样本 B，存储 1920×1080 + -90° 旋转）：4,645,300 → 2,068,829 B，SSIM 0.9888，相册动态正常。
- 输出追加 `vivoMediaExtInfo` uuid box 后，真机替换、媒体扫描、JPG 未改动均验证通过。
- 实测 uuid box 大小有 359 B 和 493 B 两种，代码按原字节复制，不做假设。
- ffmpeg 7.1.1 / ffprobe 7.1.1 验证通过；更低版本未测试。
- HDR、10-bit、BT.2020、4K、HEVC 尚未在真机验证，当前默认跳过。
- 其他厂商的实况照片机制（小米、OPPO、三星等）未验证；Studio 只识别 `IMG_YYYYMMDD_HHMMSS.jpg + .mp4` 这一种命名。

## Android 版本

- **最低**：Android 8.0（API 26）。更老的 toybox 可能缺少 `du -a` 的某些行为，未验证。
- **推荐**：Android 10+。Scoped Storage 对 `adb shell` 影响较小，但部分 OEM 会限制 `/sdcard/Android/data`。
- Android 11+ 的“最近删除”/回收站机制可能让部分文件仍然占用空间，工具会按实际路径统计。

## 厂商注意事项

### vivo / iQOO（OriginOS）

- 已验证：`/sdcard/Android/data` 可读，`du`、`md5sum`、`content query` 均可用。
- 隐私空间是 User 666，应用分身是 User 999；`pm list users` 能看到，但本工具只扫描 User 0。
- `dumpsys diskstats` 的数字可能长期不刷新（例如 Videos Size 停在旧值），不要作为现值。

### 小米 / Redmi（MIUI / HyperOS）

- 通常需要开启“开发者选项 → USB 调试”，部分版本还需要“USB 调试（安全设置）”才能做某些操作；文件读取一般不需要。
- “MIUI 优化”可能影响后台 ADB 稳定性，扫描时保持屏幕解锁。

### OPPO / OnePlus / realme（ColorOS / OxygenOS）

- 部分版本有“禁止权限监控”或“USB 安装”开关，不影响文件读取。
- 多用户/分身同样会被 `pm list users` 列出，本工具不会触碰。

### 华为 / 荣耀（HarmonyOS / EMUI）

- 连接模式选择“传输文件”；“仅充电”模式下 ADB 可能不可用。
- HarmonyOS NEXT 不再兼容 Android ADB 应用生态，`adb` 行为可能不同；未验证。

### 三星（One UI）

- 一般直接可用；`/sdcard/Android/data` 在 Android 11+ 可能只部分可见。

### Google Pixel / AOSP

- 预期兼容；`du`、`md5sum`、`content` 均为标准 toybox 工具。

## 桌面系统

| 系统 | 状态 | 备注 |
|---|---|---|
| macOS | 已验证 | `brew install android-platform-tools` |
| Linux | 预期兼容 | 需要 udev 规则；`apt install android-tools-adb` |
| Windows | 预期兼容 | 需要 platform-tools 在 PATH；`restore.sh` 用 Git Bash/WSL 执行 |
| WSL | 有限支持 | USB 需要 usbipd 转发；更推荐原生 Windows |

## ADB 版本

- 建议使用 Android Platform Tools **33+**（adb 1.0.41+）。
- 旧版 adb 对含空格参数的处理可能不同；如遇路径解析问题，请先升级 platform-tools。

## 多设备与多用户

- 多台设备同时连接时，必须用 `--serial` 或 `ANDROID_SERIAL` 指定。
- 工具只处理 **User 0** 的 `/sdcard`。
- 隐私空间、应用分身、工作资料（User 10/11/666/999 等）不会被扫描或删除。

## 已知限制

1. `du` 统计的是 apparent size，和 `df` 的块占用可能不同。
2. 文件名含换行/反斜杠时，MD5 阶段会跳过该文件（不会误删）。
3. 部分 OEM 限制 `/sdcard/Android/data` 的 shell 访问，`apps` 只能显示可见部分。
4. `dumpsys diskstats` 可能缓存旧值。
5. 不支持 iPhone / iPad。
