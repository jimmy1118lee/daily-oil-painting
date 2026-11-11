# AGENTS.md

给在本仓库工作的编码 agent 的规则。**动手前先读 `HANDOVER.md`**，那里记录了全部历史坑。

## 这是什么

Windows 桌面壁纸程序：每天自动把一幅 **1300–1800 年的欧洲古典油画**设为壁纸。
Python 3.12 + Pillow + pystray，无其它第三方依赖。**仅支持 Windows**。

## 常用命令（都在项目根目录执行）

| 目的 | 命令 |
| --- | --- |
| **冒烟测试（离线，改完先跑这个）** | `python tools/smoke_test.py` |
| 体检（托盘/自启/守护/作品库/显示器） | `python main.py diagnose` |
| 立刻换一张 | `python main.py change` |
| 重建作品库（联网，约 10 分钟） | `python main.py catalog --force` |
| 查看状态 | `python main.py status` |
| 只渲染不换壁纸（看排版效果） | `python main.py render 图片.jpg --out preview.jpg` |
| 确认托盘在跑 / 拉起托盘 | `python main.py watchdog` |
| 打包独立 exe | 见 `HANDOVER.md` 的「构建」一节 |

## 必须遵守

1. **绝不把 Harvard API Key 写进代码、文档或提交**。它只存在于
   `%LOCALAPPDATA%\DailyOilPainting\config.json` 或环境变量
   （`DOP_HARVARD_API_KEY` / `HARVARD_API_KEY`）。
   `tools/smoke_test.py` 会扫描仓库，发现泄漏直接 FAIL。
2. **任何可能进日志的字符串都要当心密钥**：拼 URL 用 `dop.http.redact()`；
   日志脱敏过滤器挂在 handler 上（不是 logger 上，见 HANDOVER 的坑 #4）。
3. **改 `src/dop/sources/*` 之后必须跑真实接口验证**，不要照文档改代码。
   四家馆每家都踩过"文档没写"的坑，各文件顶部注释里有清单。
4. **pythonw 下 `sys.stdout` / `sys.stderr` 是 `None`**。任何输出前先
   `dop.util.ensure_std_streams()`；异常必须走 `get_logger(...)`，
   只 `print` 在托盘/独立 exe 下等于丢弃。
5. 改完渲染或壁纸相关代码，除了跑冒烟测试，还要**亲眼看一眼**
   `python main.py render` 生成的预览图（尺寸、构图、水印、多屏接缝）。
6. 保持 Windows-only。不要为"跨平台兼容"引入新依赖或抽象层。

## 代码约定

- 注释、日志、面向用户的文案用中文；标识符用英文。
- 每个 `sources/*.py` 顶部有一段"踩过的坑"注释，改之前先读。
- 新增图源：实现 `fetch(cfg, http, log, progress) -> list[dict]`，
  用 `base.make_work()` 构造作品，在 `sources/__init__.py` 的 `SOURCES` 注册一行。
- 所有作品必须过 `base.looks_like_painting()`（按**媒材**判定，不是按部门）。
- 不要动数据目录结构（`%LOCALAPPDATA%\DailyOilPainting`），用户已有历史数据。
