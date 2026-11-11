# 缺陷测试报告

测试日期：2026-11-11（Asia/Hong_Kong）。本轮仅测试和记录，未修改业务代码、真实配置、壁纸或自启设置。

后续更新：用户要求排除竖图后，选图流程增加方向检查，同时修复了 BUG-05 的小作品库重复重试问题。当前缺陷审计为 **26 项：17 通过、9 失败**，其余 7 类缺陷尚未修复；下面保留最初审计结果作为历史记录。横图筛选的独立回归测试 `python tools/test_landscape.py` 为 **14/14 通过**。

## 测试结果

| 检查 | 结果 |
| --- | --- |
| 原有 `python tools/smoke_test.py` | **8/8 PASS，退出码 0** |
| `python main.py diagnose` | 当前日志、托盘、自启、双屏和壁纸状态正常 |
| 新增 `python tools/bug_audit.py` | **26 项：15 通过、11 失败，无测试执行错误** |
| 稳定性 | 最终审计连续运行两次，均复现相同的 11 个失败项 |

11 个失败断言合并为下述 **8 类缺陷**。新测试退出码 1 表示确实复现了尚未修复的缺陷，不是原有基线变红。

Python 3.12.10，Windows，当前双屏均为 3840×2160，桌面画布 7680×2160。真实作品库读取到 1496 幅。沙箱会造成临时目录访问失败，因此最终验证在沙箱外运行。

## 已复现的缺陷

### BUG-01 · P1 · 强制重建失败会清空原有作品库

- **触发**：已有可用目录，执行强制重建时所有启用图源请求失败。
- **实际**：旧目录的 `works` 被覆盖成空列表，后续无法从原有目录选图。
- **预期**：重建失败时保留原目录，返回错误或明确说明沿用旧目录。
- **复现**：`python tools/bug_audit.py CatalogTests.test_failed_forced_rebuild_preserves_usable_catalog`
- **证据**：测试先写入 1 幅作品，模拟图源网络失败，调用实际 `ensure_catalog(force=True)` 后磁盘上的作品数变为 0。同样场景不强制刷新则能保留旧库。
- **位置**：`src/dop/catalog.py:141`。`force=True` 使 `cached=None`，同时关闭了后续的旧库回退，最终保存了空的新目录。
- **建议**：始终读取旧库作为失败回退，仅让 `force` 控制是否跳过缓存命中；不要用失败结果覆盖已有可用数据。

### BUG-02 · P1 · 异常堆栈绕过日志密钥脱敏

- **触发**：异常消息包含凭据，业务通过 `logger.exception(...)` 记录堆栈。
- **实际**：普通日志消息被过滤，但随后格式化的异常堆栈保留了未脱敏内容。
- **预期**：最终写入日志的消息、异常及异常链全部脱敏。
- **复现**：`python tools/bug_audit.py SecretTests.test_exception_traceback_is_also_redacted`
- **证据**：通过实际 `setup_logging` 注册虚构测试密钥，再由子 logger 记录异常；日志文件的堆栈中仍能找到测试密钥。没有使用真实密钥，也未将真实日志写入报告。
- **位置**：`src/dop/util.py:38`。过滤器只处理 `record.getMessage()`，未覆盖格式化阶段追加的 `exc_info` / 异常链。
- **建议**：在最终格式化输出后统一脱敏，并覆盖同一记录被多个 handler 格式化的情形。

### BUG-03 · P2 · “重新下载当前画作”实际更换成另一幅画

- **触发**：托盘点击“重新下载当前画作”，或执行 `change --redownload`。
- **实际**：两条入口都重新随机选图，原来的画作被换掉。
- **预期**：保留当前作品身份，强制重新获取该作品的图片。
- **复现**：`python tools/bug_audit.py RedownloadTests`
- **证据**：隔离目录中当前作品与历史均为 `met:1`，目录包含 `met:1` 与 `met:2`；执行真实托盘/CLI → 引擎 → 渲染 → 状态记录链路后，当前作品变为 `met:2`。只有下载和系统壁纸调用被替换，没有操作真实桌面。
- **位置**：`src/dop/tray.py:153`、`src/dop/cli.py:58`、`src/dop/engine.py:170`。两条入口没有传当前作品，`force_new` 仅控制下载是否绕过图片缓存。
- **建议**：读取当前作品后显式传给 `Engine.apply(work=..., force_new=True)`；当前作品为空时明确提示。

### BUG-04 · P2 · 修改年代或禁用图源后，旧缓存继续参与选图

- **触发**：作品库仍在有效期内时，收窄 `date_range` 或将某图源 `enabled` 设为 `false`。
- **实际**：年代改成 1700–1800 后，仍返回含 1400 年作品的目录；禁用 Met 后仍实际选中 Met 作品。
- **预期**：后续选图遵守当前配置，必要时使旧目录失效并重建。
- **复现**：`python tools/bug_audit.py CatalogTests.test_changed_date_range_does_not_reuse_incompatible_cache CatalogTests.test_disabled_source_is_not_selected_from_fresh_cache`
- **位置**：`src/dop/catalog.py:142`、`src/dop/engine.py:65`。缓存命中仅检查时间和非空，选图也没有复核图源是否启用或年代是否符合当前设置。
- **建议**：缓存记录影响筛选的配置；配置变化后失效或过滤，并在选图时遵守当前启用图源。

### BUG-05 · P2 · 小作品库的失败重试会重复尝试同一张坏图

- **触发**：排除失败作品后，可用作品不足 20 幅，但仍有正常候选。
- **实际**：失败排除集合被忽略；同一次更换的 4 次尝试可能全部耗在同一张坏图上。
- **预期**：只要仍有未失败的候选，就优先尝试不同作品。
- **复现**：`python tools/bug_audit.py SelectionTests.test_skipped_work_is_avoided_when_healthy_alternative_exists SelectionTests.test_apply_download_failure_retries_different_work`
- **证据**：两幅作品分别模拟坏图与好图，固定随机选择顺序；实际 `Engine.apply()` 的下载尝试序列是 `met:0 → met:0 → met:0 → met:0`，好图一次也未尝试。固定随机序列用于稳定复现真实可能出现的抽样结果。
- **位置**：`src/dop/engine.py:73`。只有过滤后至少还剩 20 幅时才应用排除结果。
- **建议**：过滤后只要非空就应用；本轮已失败候选不应再次消耗同一轮重试次数。

### BUG-06 · P2 · 截断的下载被当成完整图片保存

- **触发**：服务端声明 `Content-Length: 4096`，实际只发送 2048 字节后关闭连接。
- **实际**：真实 HTTP 客户端返回下载成功，并将不完整文件改名为最终文件。
- **预期**：检测声明长度与实际长度不符，清理部分文件并返回可重试错误。
- **复现**：`python tools/bug_audit.py HttpTests.test_truncated_download_is_not_accepted_as_complete`
- **证据**：测试通过本机 `127.0.0.1` HTTP 服务返回截断内容，执行实际 `HttpClient.download()`；未访问外部网站。
- **位置**：`src/dop/http.py:189`、`src/dop/http.py:204`。长度头只用于大小上限，结束后只检查内容是否至少 1024 字节，没有检查是否完整。
- **建议**：在已知有效长度时校验完整性；图片解码失败也应纳入图片候选回退，避免坏缓存长期复用。

### BUG-07 · P2 · 双屏壁纸文件对调后，守护误判为正常

- **触发**：两块显示器使用不同切片，系统或其他程序将两张文件对调。
- **实际**：`wallpaper_matches()` 返回 `True`，守护不会恢复。
- **预期**：按显示器身份或矩形核对对应的文件，而不只是检查文件集合。
- **复现**：`python tools/bug_audit.py RenderTests.test_guard_detects_wallpapers_swapped_between_monitors`
- **证据**：通过实际 `Engine.apply()` 为 320×180 与 160×240 两块模拟屏生成跨屏切片，正常映射校验通过；模拟系统将映射互换后仍校验通过。系统设置调用已替换。
- **位置**：`src/dop/engine.py:340`。期望路径和实际路径都转为集合，丢失了“哪块屏对应哪个文件”的信息。
- **建议**：在当前作品记录中保存显示器与文件映射，并逐屏比较；兼容已有记录格式。

### BUG-08 · P2 · CLI 设置密钥后会原样回显

- **触发**：使用 `config set sources.harvard.api_key ...` 设置密钥。
- **实际**：成功提示直接打印原始值，与 `config get` / `config show` 的打码行为不同。
- **预期**：设置敏感字段后的确认消息同样打码。
- **复现**：`python tools/bug_audit.py SecretTests.test_secret_config_set_does_not_echo_plaintext`
- **证据**：仅传入虚构测试密钥并截获标准输出；保存函数已替换，没有改真实配置。
- **位置**：`src/dop/cli.py:158`。`value!r` 未先检查 `is_secret_path`。
- **建议**：输出前按字段类型打码。

## 已通过的补充测试

15 项通过：未变更且新鲜的目录复用、过期目录重建、自动重建失败保留旧库、目录去重、下载超限保留旧文件并清理部分文件、媒材筛选、年代边界、四种渲染模式尺寸、负坐标/纵向双屏切片、暂停不更换也不恢复、到时更换及当天去重、关闭补换时的行为、错过昨日的补换、避免立即重复上幅作品、大作品库排除坏图。

## 文件与复跑

- `tools/bug_audit.py`：26 项可重复运行的行为测试，使用标准库 unittest，无新增依赖。
- `tools/bug_audit_results.txt`：最后一次完整审计输出，包含失败堆栈；其中的密钥字符串均为虚构测试数据。

```powershell
python tools/smoke_test.py
python tools/bug_audit.py
```

建议先处理 BUG-01 与 BUG-02，再处理重新下载和筛选配置问题。本轮没有修复或部署变更，因此审计失败项仍可复现。

## 范围与限制

本轮执行了真实环境的离线冒烟测试与体检，以及隔离的业务行为测试。没有启动新的后台托盘、没有真实换壁纸、没有改动真实配置或计划任务、没有执行安装同步、没有扫描或导出真实密钥。未验证远端博物馆接口、完整作品库在线重建、打包 exe、实际重启后的登录自启、长时间运行和所有 GUI 交互；不能据此断言软件不存在其他缺陷。哈佛图片暂时不可达等交接文档已知限制不计入新增缺陷。
