# 安全模型

这份文档说明 `adbclean` 的边界：它保证什么、不保证什么、以及在按下 `--apply` 之前你应该检查什么。

## 设计目标

1. **不 Root、不改系统。** 所有操作都通过 ADB 调用手机上的普通 shell 命令完成。
2. **默认只读。** `scan` / `report` / `live` / `apps` / `junk`（不带 `--apply`）都不会删除任何东西。
3. **白名单删除。** 只有明确列出的目录才能被 `junk` 删除；白名单之外的路径会被安全护栏拒绝。
4. **不碰个人文件。** 照片、视频、聊天记录、文档、下载文件永远不会被 `junk` 删除。
5. **至少保留一份。** 去重只删除 MD5 完全相同的副本，并且每组保留一份。
6. **可追溯。** 每次删除都会在本机生成 TSV 清单。
7. **不碰隐私空间。** 只处理当前用户（User 0）的 `/sdcard`。

## 威胁模型

| 风险 | 缓解措施 |
|---|---|
| 误删个人照片/视频 | `junk` 白名单不包含 `DCIM`、`Pictures`、`Movies` 等目录；`dupes` 至少保留一份；`live` 只删 MP4 且要求同名 JPG 存在 |
| 误删应用原片 | `Android/data` 下只自动删缓存类副本；DNG/INSP/数据库等默认保护 |
| 选错设备 | 多设备时必须 `--serial`；操作前打印序列号（报告里脱敏） |
| 碰到隐私空间/应用分身 | 扫描根目录和删除路径都会拒绝 `/storage/emulated/<非0>` 与 `/data/user/<非0>` |
| 缓存清单过期 | 删除前重新校验白名单；`dupes` 删除前重新校验保留副本 MD5；哈希缓存绑定扫描时间戳 |
| 文件名注入 | 所有远端路径都经过 POSIX 单引号转义；拒绝包含 `..` 和 NUL 的路径 |
| 报告泄露隐私 | 报告只写本地；序列号默认脱敏；不含文件内容 |

## 删除白名单（精确规则）

`junk` 只允许删除以下目录：

```text
/sdcard/.thumbnails
/sdcard/DCIM/.thumbnails
/sdcard/Pictures/.thumbnails
/sdcard/Movies/.thumbnails
/sdcard/Music/.thumbnails
/sdcard/Download/.thumbnails
/sdcard/Android/data/<包名>/cache
/sdcard/Android/data/<包名>/files/cache
/sdcard/Android/data/<包名>/**/<目录名匹配 cache|thumb|tmp|temp|log>
```

最后一条的目录名必须**整段**匹配：

- 允许：`cache`、`caches`、`image_cache_new`、`thumbnails`、`thumbs`、`tmp`、`temp`、`logs`、`AVFSCache`
- 拒绝：`catalogs`、`photos`、`galleryOriginal`、`files`、`databases`、包名根目录

实现见 `src/adbclean/classify.py` 的 `CACHE_SEGMENT_RE` 与 `is_junk_dir()`。

## 去重的安全边界

`dupes` 的删除条件：

1. 文件字节大小相同；
2. 设备端 MD5 完全相同；
3. 每组至少保留一份（按 `keep_score` 选）；
4. 保留副本在删除前重新计算 MD5（默认开启，`--no-verify` 可关闭）；
5. `Android/data` 中的非缓存副本、`Android/obb` 与 `Android/media` 中的副本默认不删（`--include-app-data` 可开启）。

保留优先级：

```text
DCIM > Pictures > Movies > Music/Documents/Recordings > Download
     > 其他顶级目录 > Android/media > Android/data
     > vivoMediaData/.hide > 回收站
```

同一层级内：

- `互传`、`办公套件`、`聊天应用`、`备份`、`副本`、`迁移`、`旧手机` 降权；
- 隐藏目录降权；
- 缓存/缩略图目录降权；
- 路径更短者优先。

### 为什么哈希缓存要绑定扫描时间

只按“路径 + 大小”缓存 MD5 是不安全的：应用可能原地重写一个同大小的文件。`adbclean` 把扫描时间戳作为缓存令牌，只有同一次扫描产生的哈希才会被复用。重新 `scan` 后会自动重算。

## 动态照片的安全边界

`live --delete` 只删除同时满足以下条件的 MP4：

- 文件名严格匹配 `IMG_YYYYMMDD_HHMMSS.mp4`；
- 同目录下存在同名 JPG（`.jpg` / `.jpeg`，大小写不敏感）；
- MediaStore 时长 ≤ `--max-duration`（默认 5000 ms）；
- 排除 `Videoshot_*.mp4`、`IMG_*_1.mp4`、`*_edit_*.mp4` 等命名；
- 删除前再次确认同名 JPG 仍存在；
- 删除时只 `rm` MP4，JPG 不进入删除列表。

默认**不删除**：

- 查不到 MediaStore 时长的文件（`--include-unknown` 可包含）；
- 时长超过阈值的文件（单独列出）；
- 没有同名 JPG 的文件。

建议始终指定 `--backup DIR`。备份完成后会生成 `restore.sh`，可一键推回并触发媒体扫描。

## 实况照片压缩的安全边界

`adbclean studio` 是唯一会修改手机文件的子命令，边界如下：

- **只改伴生 MP4。** JPG / JPEG 从不写入、不重命名、不删除。
- **只处理验证过的配对。** 必须同时满足：`IMG_YYYYMMDD_HHMMSS.mp4`、同名 JPG 存在、MediaStore 时长 ≤ 5 秒。
- **先备份再替换。** 原 MP4 备份到电脑，MD5 校验通过后才 push 临时文件。
- **原子替换。** 临时文件与目标同目录，手机端 MD5 → `chmod` → 恢复 mtime → `mv -f`，避免出现半截文件。
- **失败回滚。** `mv` 后任何校验失败，立即从备份恢复；恢复也失败时停止并保留备份。
- **不确定就跳过。** HDR / 10-bit / BT.2020 / 异常像素格式 / 没有 vivo uuid / 压缩无收益 / 手机文件在压缩期间变化，全部不替换。
- **没有“跳过备份”的极速模式。** 每条替换路径都必须先有可验证的备份。

备份目录：

```text
# macOS
~/Library/Application Support/adbclean/studio/backups/
# Linux
${XDG_DATA_HOME:-~/.local/share}/adbclean/studio/backups/
# Windows
%APPDATA%\adbclean\studio\backups\
```

每个批次包含 `manifest.tsv`（原始/压缩大小、双方 MD5、mtime）和 `restore.sh`。Studio 重启后仍可从「本地备份」批量恢复。

**清除备份是不可逆操作。** 界面会二次确认；删除后无法再恢复原始 MP4。建议逐张确认相册动态正常后再删。

## 删除清单

每次删除都会写入本机：

```text
# macOS
~/Library/Caches/adbclean/manifests/
# Linux
~/.cache/adbclean/manifests/
# Windows
%LOCALAPPDATA%\adbclean\manifests\
```

文件为 TSV，包含：

- `junk-dirs`：目录、体积、文件数；
- `exact-duplicates`：MD5、单份大小、保留路径、删除路径；
- `live-photo-mp4`：删除的 MP4、保留的 JPG、时长、日期。

清单不会上传到任何地方。

## 执行前检查清单

- [ ] `adb devices` 只有一台目标设备，状态是 `device`；
- [ ] `adbclean info` 的型号/序列号正确；
- [ ] `adbclean junk` 的列表里没有你认识的个人目录；
- [ ] `adbclean dupes` 的“保留”列是你希望保留的目录；
- [ ] 动态照片已备份（如果在意动效）；
- [ ] 手机电量充足，扫描/删除期间不要锁屏或拔线。

## 出问题怎么办

1. **误删了缓存目录**：应用会重新生成，通常无需处理。
2. **误删了重复文件**：查看清单里的 MD5 和保留路径；如果保留副本还在，数据没有丢失。
3. **误删了动态照片 MP4**：如果用了 `--backup`，进入备份目录执行 `./restore.sh`。
4. **压缩后相册动态异常**：打开顶部「本地备份」，恢复对应批次；Studio 重启后也能恢复。
5. **设备掉线**：重新插拔，确认 RSA 授权；已完成的删除不会回滚，但清单会保留。
6. **报告数字异常**：重新 `scan`；不要依赖 `dumpsys diskstats` 的缓存值。

## 不保证的事情

- 不保证清理后空间一定增加（缓存会重建、文件系统统计有延迟）。
- 不保证能读取所有 OEM 的 `/sdcard/Android/data`（部分 ROM 会限制 shell 权限）。
- 不保证 `du` 的 apparent size 等于实际磁盘占用（压缩、稀疏文件、块对齐会造成差异）。
- 不保证能恢复没有备份的删除，或已经清除备份的压缩替换。
- 不保证电脑磁盘损坏、缓存目录被手动删除后还能恢复。
