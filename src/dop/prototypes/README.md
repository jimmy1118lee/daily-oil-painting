# 每日油画 · 控制面板原型（未定稿）

问题：桌面控制面板应该是什么样？三个结构不同的方向供比较，尚未选择正式实现。

直接双击 `gui-prototype.html`，或在项目根目录执行：

```powershell
python src/dop/prototypes/preview.py
```

通过底部切换器或键盘左右键切换，也可使用 `?variant=A`、`?variant=B`、`?variant=C`。

| 方案 | 方向 | 结构 |
| --- | --- | --- |
| A · 浅色画廊 | 推荐，大画作与常用设置兼顾 | 左侧导航、中央预览、右侧每日计划与屏幕设置 |
| B · 深色观画 | 更适合看画，弱化工具感 | 居中大预览、底部操作栏，设置收进弹层 |
| C · 轻量控制台 | 窗口更紧凑，便于日常操作 | 无侧栏，预览与信息并排，自动更换集中在下方 |

原型图：[方案 A](screenshots/prototype-A.png)、[方案 B](screenshots/prototype-B.png)、[方案 C](screenshots/prototype-C.png)。截图对应 1536×1030 的演示画布；C 的软件窗口尺寸更小。

有本机 Chrome 时，可用 `node src/dop/prototypes/capture.cjs` 重新截图。截图工具使用独立的 `_chrome-profile` 临时资料，已加入忽略规则；不会访问个人浏览器资料。

配色来自风景油画中的灰绿与天空蓝。标题用 Palatino Linotype，中文与操作区用 Microsoft YaHei UI；没有网络字体或第三方依赖。

点击“换一幅画”、收藏、切换显示器与时间设置只改变浏览器内存；刷新即复位。不会读取个人配置、启动真实托盘或更换桌面壁纸。标题栏控制按钮只用于表达 Windows 窗口外观。

样图来自已有公共领域馆藏缓存，仅用于评估设计；素材副本不包含配置或凭据：

- Landscape with Hunters — Paul Bril，1619，芝加哥艺术博物馆。
- The Waterfalls at Tivoli — Claude-Joseph Vernet，1737，克利夫兰艺术博物馆。
- Flowers and Fruit in a Porcelain Bowl — Juan de Zurbarán，约 1645，芝加哥艺术博物馆。
- Mountain Road with Travelers — Joos de Momper, II，约 1615，芝加哥艺术博物馆。

原型位于独立分支 `prototype/ui-control-panel`，正式 Tk 界面及安装副本没有改动。用户选定方向后，再重新实现正式界面。
