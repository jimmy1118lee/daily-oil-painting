# 交接说明（HANDOVER）

> 面向接手的编码 agent。读完本文你应该能直接开工，而不必把历史坑重踩一遍。
> 规则速查见 `AGENTS.md`；面向普通用户的手册见 `README.md`。

**交接时刻**：2026-11-11 13:40（Asia/Hong_Kong）
**版本**：1.0.0
**状态**：已安装并正在用户机器上运行

**后续开发（2026-11-11）**：已默认排除竖图（`selection.landscape_only=true`），兼容已有图片缓存并按 EXIF 方向判断；安装副本已同步、后台已重启。横图回归测试 14/14、冒烟测试 8/8 通过。缺陷审计当前 26 项中 17 项通过、9 项失败，详情见 `BUG_REPORT.md`。

**托盘点击修复（2026-11-11）**：默认点击动作由“现在换一张”改为“打开控制面板”，单击不再误换壁纸。控制面板通过独立 GUI 进程启动，避免在托盘线程创建 Tk，并禁止闪出控制台。右键菜单“现在换一张”保留原有行为。复现与回归命令：`python tools/test_tray_click.py`。

**GitHub 私有备份**：https://github.com/jimmy1118lee/daily-oil-painting ，分支 `main`。首次备份使用清理过敏感示例的当前快照；原始交接历史仅保留在本机 `master` 分支，不上传。个人配置、图片缓存和原始审计日志不在备份中。

---

## 1. 这是什么

一个 Windows 桌面壁纸程序：**每天自动把一幅 1300–1800 年的欧洲古典油画设为桌面壁纸**。

- 图源：大都会艺术博物馆（Met）、芝加哥艺术博物馆（AIC）、克利夫兰艺术博物馆（CMA），
  以及需要自备 API Key 的哈佛艺术博物馆。全部为公共领域 / CC0 或开放图像。
- 成品按**显示器分辨率逐屏生成**，4K 不糊、多屏不压扁。
- 三种运行形态共用同一份代码：源码安装版（当前用户用的）、独立 exe（分发给朋友）、CLI。

**技术栈**：Python 3.12 + Pillow + pystray，其它一律标准库。仅 Windows。

---

## 2. 当前状态快照

```
自动更换：运行中，每天 08:30（开机补换：开）
开机自启：开（登录任务/定时看护/注册表项 三项齐全）
后台托盘：运行中
壁纸守护：每 300 秒检查一次
作品目录：1496 幅（生成于 2026-11-11 13:23:24，有效期 14 天）
    · met 267  · artic 529  · cleveland 280  · harvard 420
当前壁纸：《Danaë》Orazio Gentileschi c. 1623（克利夫兰）
    逐屏 7680×2160（两块 3840×2160）
```

**用户环境**：Windows 11（10.0.26200）、双显示器 3840×2160 @150% 缩放、
Python 3.12.10 位于 `C:\Users\v_djyjli\AppData\Local\Programs\Python\Python312\python.exe`。

---

## 3. 关键路径

| 用途 | 路径 |
| --- | --- |
| 源码 | 本仓库 |
| **已安装副本**（用户实际在跑的） | `%LOCALAPPDATA%\Programs\DailyOilPainting` |
| **数据目录** | `%LOCALAPPDATA%\DailyOilPainting` |
| 日志 | `…\logs\dop.log`（滚动 2 MB × 3） |
| 配置 | `…\config.json`（**含 Harvard Key，不要外传**） |
| 作品目录缓存 | `…\state\catalog.json` |
| 更换记录 | `…\state\current.json`、`history.json` |
| 成品壁纸 | `…\wallpapers\`（保留最近 12 个文件） |
| 分发件 | 仓库 `dist_dir\`（独立版 zip 30 MB + 源码 zip 118 KB） |
| 计划任务 | `DailyOilPainting`（登录时）、`DailyOilPainting-Watchdog`（每 10 分钟） |
| 看门狗脚本 | `%LOCALAPPDATA%\DailyOilPainting\watchdog.bat` |

**改完代码要同步到安装副本**：`python src\bootstrap.py install --no-launch`
（会把源码复制到 `%LOCALAPPDATA%\Programs\DailyOilPainting` 并重设自启/快捷方式）。
只改仓库不复制，用户跑的还是旧代码——这一点非常重要。

---

## 4. 架构与数据流

```
sources/{met,artic,cleveland,harvard}.py
        │  fetch(cfg, http, log, progress) -> list[work]
        ▼
catalog.py ──► state/catalog.json     （TTL = 各图源条款的最小值，见 §7）
        │
        │ 托盘每 30 秒 tick 一次；或控制面板点“换一张”
        ▼
engine.py ──► 选图（去重 / 轮换 / 图源降权）
        │      ──► http.py 下载原图（缓存到 cache/<source>/）
        │      ──► render.py 合成（每块屏一张 + 一张跨屏图）
        │      ──► wallpaper.py 逐屏设置（IDesktopWallpaper COM）
        ▼
state/current.json + history.json ──► 托盘图标、详情页、收藏
```

### 模块职责

| 文件 | 职责 | 改动风险 |
| --- | --- | --- |
| `main.py` | 入口，把 `src` 加进 sys.path | 低 |
| `src/bootstrap.py` | 源码安装/卸载引导（只用标准库） | 中 |
| `src/dop/cli.py` | 全部子命令（gui/tray/change/catalog/status/diagnose/install/watchdog/…） | 高 |
| `src/dop/config.py` | 默认配置、深度合并、环境变量注入、密钥打码 | 中 |
| `src/dop/paths.py` | 程序目录/数据目录/**打包模式适配** | 中 |
| `src/dop/engine.py` | 选图、下载回退、渲染、逐屏设置、历史、壁纸守护判定 | **高** |
| `src/dop/catalog.py` | 抓取聚合、去重、TTL 计算、缓存读写 | 中 |
| `src/dop/render.py` | 构图（fill/fit/center）、多屏、水印、CJK 字体 | **高**（视觉） |
| `src/dop/wallpaper.py` | 显示器枚举、DPI 感知、逐屏壁纸、自启（备用）、托盘常显 | **高**（COM/ctypes） |
| `src/dop/tray.py` | 托盘图标、菜单、每 30 秒调度、壁纸守护、单实例锁 | **高** |
| `src/dop/gui.py` | tkinter 控制面板 | 中 |
| `src/dop/autostart.py` | 计划任务自启 + 看护（替代纯注册表） | 中 |
| `src/dop/installer.py` | 一键安装（配置+图标+自启+快捷方式） | 中 |
| `src/dop/shortcuts.py` | 快捷方式（PowerShell WScript.Shell） | 低 |
| `src/dop/http.py` | 限速 + 退避重试 + **URL 脱敏** | 中 |
| `src/dop/cache.py` | 带 TTL 的 JSON 响应缓存 | 低 |
| `src/dop/util.py` | 日志、脱敏过滤器、JSON 原子写、空输出流、时间工具 | 中 |
| `src/dop/icon.py` | 纯代码生成程序图标 / 托盘缩略图 | 低 |
| `src/dop/report.py` | 详情页 HTML | 低 |
| `src/dop/sources/*` | 四家博物馆接口（**每家顶部都有踩坑注释**） | 中 |
| `tools/smoke_test.py` | 离线冒烟测试 | 低 |

---

## 5. 踩过的坑（**优先读这一节**）

### 坑 1：Windows 11 不遵守 `WallpaperStyle=22`（跨屏）
- **现象**：桌面显示的不是设计稿——画作被压扁，而且两块屏各显示一份完整跨屏图。
- **根因**：注册表写 22（Span）+ `SystemParametersInfoW` 在这台机器上无效，
  系统把整张 7680 宽的图分别缩放进每块屏。
- **排查方法**：造纯色块图当壁纸、截屏后**采样像素**（不要靠肉眼看缩略图）。
- **现状**：改用 `IDesktopWallpaper` COM **逐屏设置**，给每块屏一张尺寸精确匹配的图。
  代码：`wallpaper.set_wallpapers_per_monitor()`。

### 坑 2：`IDesktopWallpaper` 的两个隐形细节
- `CoCreateInstance` 必须用 **`CLSCTX_ALL`**；用 `INPROC_SERVER` 会返回
  `REGDB_E_CLASSNOTREG`（类明明已注册）。
- 虚表下标与常见写法不同：`SetWallpaper=3`、`GetWallpaper=4`、
  `GetMonitorDevicePathAt=5`、`GetMonitorDevicePathCount=6`、`GetMonitorRECT=7`、
  `SetPosition=10`。下标错会直接**访问违例**（进程崩，不是抛异常）。
- 显示器按 **rect 匹配**（COM 的 device path 与 GDI 的 device name 不同）。

### 坑 3：DPI 感知
- 不声明 DPI 感知，4K + 150% 缩放只会拿到 **2560×1440 的逻辑分辨率**，
  渲染出的壁纸被系统放大，明显发糊。
- 已把 `set_dpi_aware()` 放进 `wallpaper.get_monitors()` 内部并做幂等，
  **任何入口都不会再拿错**。不要把它挪出去。

### 坑 4：日志脱敏必须挂到 handler 上
- 挂 logger 级 filter 只能过滤"直接由该 logger 产生"的记录；
  子 logger（`DailyOilPainting.engine` 等）**传播上来的记录不经过它**。
- Met 的 `410`、Harvard 的 `403` 都会把完整 URL 带进异常信息，URL 里有 apikey。
- 现状：`util.register_secrets()` 同时挂 logger 和**所有 handler**；
  `http.redact()` 再做一层正则兜底；另有 `python main.py scrub-logs` 清历史残留。

### 坑 5：`setup_logging` 自锁死
- 一度用非可重入 `threading.Lock`，`setup_logging` 内部又调 `register_secrets`
  去拿同一把锁 → **程序启动即挂死**，无任何输出。
- 现状：`_log_lock = threading.RLock()`，且 `register_secrets` 移到锁外调用。

### 坑 6：pythonw 下 `sys.stdout` / `sys.stderr` 是 `None`
两个后果，都真实发生过：
1. 进度输出 `sys.stdout.write(...)` 抛 `AttributeError` → `change` 命令**整个失败**，
   而且没有任何报错（异常也被吞了）。这就是曾出现"计划任务跑了但什么都没发生"的原因——**调度器跑起来的进程里调度线程静默死掉，进程还在，但再也不会自动换壁纸**。
2. `main()` 的异常处理只 `print`，等于丢弃 → "程序在跑但日志一行没有"。
- 现状：`util.ensure_std_streams()` 在 `cli.main()` / `run_tray()` / `run_gui()` 最前面调用；
  `cli.main()` 的兜底异常同时写 `get_logger("cli").exception(...)`。

### 坑 7：开机自启被"启动项优化"静默禁用（**用户上次故障的主因**）
- `HKCU\...\Run` 里项还在，但 `HKCU\...\Explorer\StartupApproved\Run` 里同名值
  是 `03 00 …`（禁用）。注册表看着正常，开机**根本不执行**。
- 后果：重启几次后托盘再没起来，**一个月没人发现**。
- 现状：`autostart.py` 用两个计划任务（登录时 + 每 10 分钟看护），
  并在 `enable()` 时把 `StartupApproved` 改回 `02 00 …`；注册表项只作备用。
- **不要退回纯注册表方案。**

### 坑 8：pystray 在受限令牌下创建托盘窗口失败
- `pystray` 在 `_create_window` 里调 `ChangeWindowMessageFilterEx`，
  在受限/沙箱环境下返回 `WinError 5`，异常抛出。
- **已做架构解耦**：调度线程与托盘图标无关，图标起不来也继续无图标后台运行
  （`tray.run()` 先起 scheduler，再 `_start_icon()`）。**不要把它改回串行**。

### 坑 9：PyInstaller `--onefile` 在受限环境起不来
- 报 `[PYI-xxx:ERROR] Could not create temporary directory!`——onefile 需要解包到
  系统临时目录。
- 现状：用 **`--onedir`**（不需要临时目录，启动更快）。分发的是 zip 里面的文件夹。

### 坑 10：从"沙箱工作区内"运行打包程序会假性失败
- 同一份 exe 在工作区内运行：写不了日志（`Permission denied`）、
  托盘自检失败；复制到工作区外立刻全部正常。
- **这是测试环境的限制，不是代码缺陷。** 判断方法：看 `diagnose` 的
  「可写入」和「系统托盘窗口自检」两项；若在别处跑正常就不用管。

### 坑 11：桌面壁纸会被系统/第三方软件重置
- 用户机器上壁纸被改回 Windows 默认 `img0.jpg`，而当时托盘没在跑。
- 现状：**壁纸守护**（`engine.wallpaper_matches()` / `restore_wallpaper()`，
  由 `tray._tick` 每 `advanced.wallpaper_guard_seconds` 秒调用）。
  暂停自动更换时不干预——这是用户"想自己换壁纸"的出口。

### 坑 12：Met 搜索接口下线（**曾让 Met 静默失效一个多月**）
- `/public/collection/v1/search` 已于 **2026-10-01 退役**，之后一律 `410 Gone`。
- 替代：`/public/collection/v1.1/search`，参数相同但**分页**：
  `offset`/`limit`（limit ≤ 500，offset+limit ≤ 10000），且**不要求 `q`**，
  可以只给 `departmentId + dateBegin/dateEnd + hasImages`。
- 现状：`met.py` 已迁移；dept 11+15 在该区间约有 3273 条候选。

### 坑 13：Met 限流
- 官方限 80 次/秒，但实测 8 线程猛刷会被大面积 403。
- 现状：`advanced.requests_per_second = 5`、`concurrency = 3`。
  曾试过 8 次/秒 → 接口失败率 17%、重建 13 分钟，已回退。

### 坑 14：AIC
- 图片 CDN 对请求头敏感，必须带浏览器 UA + **`AIC-User-Agent`**（`http.py` 已默认带）。
- IIIF 尺寸有上限，大尺寸会 403 → `artic.py` 有 `2400 → 1680 → 843 → 600` 回退。
- 搜索接口用 Elasticsearch 风格 POST body；`q` 与 `query` 同时给会互相干扰。

### 坑 15：克利夫兰（CMA）部门里混着雕塑
- `European Painting and Sculpture` 部门里有约 **28% 是铜雕/大理石/陶塑/镀金木雕**。
- 现状：服务端加 `type=Painting`，客户端再用媒材校验。

### 坑 16：哈佛（Harvard）——踩得最多
1. **只能用查询参数 `apikey` 鉴权**，`X-Api-Key` 请求头一律 401 → 必须做 URL 脱敏。
2. `culture=Italian` 返回 0，**必须小写 `italian`**；`culture=European` 这个取值不存在。
   现状改用 `q=division:"European and American Art"`。
3. `division` 会把**美国绘画**一起带出来 → 客户端按 `EUROPEAN_CULTURE_KEYS` 白名单过滤。
4. `yearmade=1300-1800` 是**"有交集"语义**，会漏进年代跨界作品 → 客户端严格复核。
5. `primaryimageurl` 只是 **IIIF 基址**，要自己拼 `/full/<尺寸>/0/default.jpg`。
6. `imagepermissionlevel`：**0=可任意尺寸**，1=最多 256px，2=禁止 → 只能取 0。
7. **图片服务器（nrs.harvard.edu / ids.lib.harvard.edu）在用户网络上持续 403/429**
   （换 HTTP 栈、等待、换头均无效；同域名 IIIF manifest 却是 200）。
   → 目前**只有元数据可用，取不到图**。`engine.apply()` 会自动跳过并换下一幅。
8. **使用条款**：内容缓存 ≤ 2 周、每天 ≤ 2500 次调用、必须回链、仅限非商业。
   现状：`catalog.effective_ttl_days()` 会因哈佛把整体 TTL 压到 14 天；
   超过两周的哈佛缓存原图会被 `engine._prune_source_images()` 清掉。

### 坑 17：艺术筛选必须按"媒材"，不能按"部门"
- Met 的"欧洲绘画"部门里混着象牙器和素描；CMA 的绘画部门里混着雕塑。
- 现状：`sources/base.py` 的 `looks_like_painting()` 要求媒材命中
  `oil / tempera / fresco / encaustic / distemper`，且黑名单排除
  `ivory / marble / bronze / vellum / paper / 素描类…`。

### 坑 18：同一台图片服务器会"连坐"
- 429/403/5xx 说明整台在拒绝我们，换同主机的其它尺寸地址纯属浪费时间
  （单个哈佛作品的失败代价曾高达 36 秒）。
- 现状：`engine.download()` 用 `blocked_hosts` **短路同主机备用地址**，降到约 7 秒。

---

## 6. 验证方法（改完必做）

```bat
:: 1) 离线冒烟测试（8 项，不联网、不改系统）
python tools\smoke_test.py

:: 2) 体检：托盘/自启/守护/作品库/显示器/密钥打码
python main.py diagnose

:: 3) 真换一张（会联网下载 + 改壁纸）
python main.py change

:: 4) 只渲染预览（不改壁纸，用来看排版）
python main.py render 某张图.jpg --out preview.jpg
然后打开 preview.jpg 看：构图、水印、多屏接缝有没有断层
```

**验收基线**（交接时实测通过）：
- `smoke_test.py` → 8/8 PASS
- `diagnose` → 日志可写入 = 是；作品库 1496 幅；托盘自检通过
- 逐屏壁纸记录的路径都指向 `wallpapers\dop-*.jpg`

---

## 7. 配置速查

配置文件：`%LOCALAPPDATA%\DailyOilPainting\config.json`（改完 30 秒内生效，不用重启）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `schedule.time` | `08:30` | 每天几点换 |
| `schedule.catch_up` | `true` | 错过时间后开机补换 |
| `schedule.paused` | `false` | 暂停（同时会停掉壁纸守护） |
| `date_range` | `[1300,1800]` | 年代区间 |
| `image.style` | `fit` | `fit` 完整显示 / `fill` 铺满裁切 / `center` |
| `selection.landscape_only` | `true` | 排除竖图（高度大于宽度），已有缓存也生效；方向检查不会降低图源健康度 |
| `image.multi_monitor` | `each` | 每屏一份完整版式（推荐）/ `primary` / `span` |
| `image.show_caption` | `true` | 左下角作品信息 |
| `advanced.wallpaper_guard_seconds` | `300` | 壁纸守护间隔，0=关闭 |
| `advanced.max_download_attempts` | `4` | 一次更换最多试几幅 |
| `advanced.requests_per_second` | `5` | 全局限速（调高会被 Met 限流） |
| `advanced.concurrency` | `3` | 明细并发数 |
| `sources.harvard.cache_days` | `14` | **条款上限，调大无效** |
| `sources.harvard.api_key` | `""` | 建议留空并用环境变量 |

---

## 8. 构建与分发

```powershell
cd <项目根目录>
python -m pip install pyinstaller

python -m PyInstaller --noconfirm --clean --onedir --windowed `
  --name DailyOilPainting `
  --icon "$PWD\assets\app.ico" `
  --add-data "$PWD\assets;assets" `
  --paths "$PWD\src" `
  --hidden-import pystray._win32 `
  --hidden-import PIL._tkinter_finder `
  --distpath "$PWD\dist_dir" --workpath "$PWD\build_dir" --specpath "$PWD\build_dir" `
  "$PWD\packaging\entry.py"
```

注意：
- **`--add-data` 与 `--icon` 必须用绝对路径**。配合 `--specpath` 时相对路径会解析到
  spec 目录，报 `Unable to find '…\build\assets'`。
- 必须 `--onedir`（见坑 9）。
- 产物在 `dist_dir\DailyOilPainting\`，再把 exe 改成中文名、附 `使用说明.txt`、压缩成 zip。
- 源码包：打包 `main.py / README.md / 分享说明.md / requirements.txt /
  *.bat / .gitignore / assets / src / packaging / tools`，**排除 `dist_dir`、`__pycache__`**。

---

## 9. 待办与已知限制

**已知限制（不是 bug，能接受就别动）**
1. **哈佛图片取不到**（网络层 403/429）。元数据照常进库，取图失败会自动换下一幅。
   网络恢复后会自动生效，无需改代码。
2. **作品库重建很慢**（约 10 分钟）：Met 明细要一条条查，为防封做了限速。
   到期（14 天）后的那次更换会因此变慢，期间旧壁纸不动。
3. **多屏 `each` 是权宜方案**：程序拿不到"哪块屏物理在左边"，只能每屏各显示一份完整构图。
4. **托盘图标在 Win11 默认被折叠**进 `^` 溢出区。可靠入口是桌面快捷方式→控制面板。
5. 未做代码签名，首次运行会有 SmartScreen 提示。
6. 仓库**没有 git 历史**（只有一个 `.gitignore`）。建议接手后 `git init` 并提交一次基线。

**建议的下一步（按价值排序）**
1. `git init` + 首次提交（当前工作区是一堆未版本化的文件，改坏了没法回滚）。
2. 把作品库重建改成**可中断/增量**：目前中断就得整轮重来。
3. 补一组 pytest 单测（现在只有 `tools/smoke_test.py` 的冒烟级覆盖）；
   优先覆盖 `render.split_by_monitor`、`catalog._dedupe`、
   `sources/base.looks_like_painting`、`engine.choose` 的轮换逻辑（纯函数，好测）。
4. Met 接口是定时炸弹：v1.1 的 `offset+limit ≤ 10000` 限制了可枚举量。
   若哪天又变，官方建议改用 Open Access CSV（`github.com/metmuseum/openaccess`）。
5. 可选：给托盘图标加"固定到任务栏"引导（现在只在控制面板里有一句提示）。

---

## 10. 交接注意

- **不要提交 Harvard Key**。它只在 `%LOCALAPPDATA%\DailyOilPainting\config.json`
  或环境变量里。仓库任何文件都不该出现它的**任何片段**（连前 8 位都不要写进文档，
  包括交接文档和提交信息）。
- **改完必须同步到安装副本**，否则用户跑的还是旧代码：
  `python src\bootstrap.py install --no-launch`
- 用户的**数据目录里有一个月的历史和缓存**，不要为了调试去删。
- 卸载（会移除计划任务、快捷方式、注册表项）：
  `python main.py uninstall --purge` 或双击 `uninstall.bat`。
- 排查顺序建议：`status` → `diagnose` → `logs\dop.log` 尾部 → 再看代码。
