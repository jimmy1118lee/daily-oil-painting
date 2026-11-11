"""离线冒烟测试：不联网、不改系统，快速验证核心链路。

用法（在项目根目录执行）::

    python tools/smoke_test.py

退出码 0 = 全部通过；非 0 = 有失败项。
适合每次改完代码跑一遍，比手工点界面快得多。

覆盖范围
--------
1. 所有模块能导入（含 tkinter / pystray）
2. 配置能加载；密钥在展示时会打码，且**不会出现在项目文件里**
3. 渲染链路：合成一张画作 → 渲染 → 成品尺寸等于桌面色域尺寸
4. 壁纸层：能枚举显示器、能读回逐屏壁纸设置
5. 作品库：能读取且计数自洽
6. 自启模块：只读检查，不改动系统
7. 壁纸守护判定：能拿到比较结果，不抛异常
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

results: list[tuple[bool, str, str]] = []


def check(name: str):
    """装饰器：把一个无参函数当作检查项。"""

    def wrapper(func):
        try:
            detail = func() or ""
            results.append((True, name, str(detail)))
        except Exception as exc:  # noqa: BLE001
            results.append((False, name, f"{type(exc).__name__}: {exc}"))
        return func

    return wrapper


# --------------------------------------------------------------------------- 1. 导入
@check("模块导入")
def _imports() -> str:
    import importlib

    modules = [
        "dop", "dop.util", "dop.paths", "dop.config", "dop.http", "dop.cache",
        "dop.catalog", "dop.render", "dop.wallpaper", "dop.engine", "dop.cli",
        "dop.tray", "dop.gui", "dop.report", "dop.icon", "dop.shortcuts",
        "dop.installer", "dop.autostart",
        "dop.sources", "dop.sources.met", "dop.sources.artic",
        "dop.sources.cleveland", "dop.sources.harvard",
    ]
    for name in modules:
        importlib.import_module(name)
    return f"{len(modules)} 个模块"


# --------------------------------------------------------------------------- 2. 配置与密钥
@check("配置加载与密钥打码")
def _config() -> str:
    from dop.config import load_config, mask_secrets

    cfg = load_config()
    key = str(((cfg.get("sources") or {}).get("harvard") or {}).get("api_key") or "").strip()
    masked = repr(mask_secrets(cfg))
    if key and key in masked:
        raise AssertionError("打码后的配置里仍包含明文 Key")
    if key and key in repr(cfg):
        # 明文只应存在于内存中的 cfg，打码副本不能有
        pass
    return "有 Key 且展示时已打码" if key else "未配置 Harvard Key"


@check("项目文件不含明文 Key")
def _no_leak() -> str:
    from dop.config import load_config

    key = str(((load_config().get("sources") or {}).get("harvard") or {}).get("api_key") or "").strip()
    if not key:
        return "无 Key 可泄漏"
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {
            ".py", ".md", ".txt", ".bat", ".json", ".spec", ".cfg", ".ini"
        }:
            continue
        if "dist_dir" in path.parts:
            continue
        try:
            if key in path.read_text(encoding="utf-8", errors="ignore"):
                hits.append(str(path.relative_to(ROOT)))
        except OSError:
            continue
    if hits:
        raise AssertionError("以下文件含明文 Key: " + ", ".join(hits))
    return "扫描通过"


# --------------------------------------------------------------------------- 3. 渲染
@check("渲染尺寸与桌面色域一致")
def _render() -> str:
    from PIL import Image, ImageDraw

    from dop import render, wallpaper
    from dop.config import load_config

    cfg = load_config()
    monitors = wallpaper.get_monitors()
    if not monitors:
        raise AssertionError("没有枚举到显示器")

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "painting.png"
        img = Image.new("RGB", (1200, 1600), (52, 40, 30))
        draw = ImageDraw.Draw(img)
        draw.ellipse([300, 300, 900, 1100], fill=(220, 190, 160))
        img.save(src)

        work = {
            "title": "Smoke Test Painting",
            "artist": "Test Artist",
            "date_display": "c. 1500",
            "museum": "测试博物馆",
            "license": "CC0",
        }
        canvas = render.compose(src, monitors, cfg, work)
        want_w = max(m["right"] for m in monitors) - min(m["left"] for m in monitors)
        want_h = max(m["bottom"] for m in monitors) - min(m["top"] for m in monitors)
        if canvas.size != (want_w, want_h):
            raise AssertionError(f"成品 {canvas.size} != 桌面 {want_w}x{want_h}")

        out = render.save(canvas, Path(tmp) / f"out{render.suffix_for(cfg)}", cfg)
        if not out.exists() or out.stat().st_size < 10_000:
            raise AssertionError("成品文件没有正常写出")
        with Image.open(out) as reopened:
            if reopened.size != canvas.size:
                raise AssertionError("写出的图片尺寸不对")
    return f"{want_w}x{want_h}"


# --------------------------------------------------------------------------- 4. 壁纸层
@check("显示器枚举与逐屏壁纸读取")
def _wallpaper() -> str:
    from dop import wallpaper

    monitors = wallpaper.get_monitors()
    if not monitors:
        raise AssertionError("没有枚举到显示器")
    some_primary = any(m.get("primary") for m in monitors)
    if not some_primary:
        raise AssertionError("没有任何显示器被标记为主屏")
    current = wallpaper.describe_wallpapers()  # 只读，不修改
    return f"{wallpaper.describe_monitors(monitors)}；逐屏记录 {len(current)} 条"


# --------------------------------------------------------------------------- 5. 作品库
@check("作品库读取与计数自洽")
def _catalog() -> str:
    from dop import catalog as catalog_mod

    data = catalog_mod.load_catalog()
    if not data:
        return "尚未建立（不影响测试通过）"
    works = data.get("works") or []
    total = int(data.get("total") or 0)
    if total != len(works):
        raise AssertionError(f"total={total} 与 works={len(works)} 不一致")
    per_source = data.get("per_source") or {}
    if per_source and sum(per_source.values()) != total:
        raise AssertionError(f"per_source 合计 {sum(per_source.values())} != total {total}")
    if works:
        sample = works[0]
        for field in ("key", "source", "title", "image_url"):
            if field not in sample:
                raise AssertionError(f"作品条目缺少字段 {field}")
        low, high = data.get("date_range") or [1300, 1800]
        bad = [
            w for w in works
            if isinstance(w.get("year_start"), int)
            and isinstance(w.get("year_end"), int)
            and (w["year_start"] < low or w["year_end"] > high)
        ]
        if bad:
            raise AssertionError(f"{len(bad)} 幅作品超出年代区间")
    return f"{total} 幅 {per_source}"


# --------------------------------------------------------------------------- 6. 自启（只读）
@check("自启模块只读检查")
def _autostart() -> str:
    from dop import autostart

    enabled = autostart.is_enabled()
    detail = autostart.describe()
    return f"{'已开启' if enabled else '未开启'}（{detail}）"


# --------------------------------------------------------------------------- 7. 壁纸守护判定
@check("壁纸守护判定不抛异常")
def _guard() -> str:
    from dop.engine import Engine

    engine = Engine()
    record = engine.current()
    if not record:
        return "还没有换过壁纸"
    matches = engine.wallpaper_matches()
    return f"壁纸是否仍是本程序的：{'是' if matches else '否（守护会把它设回去）'}"


# --------------------------------------------------------------------------- 汇总
def main() -> int:
    failed = [r for r in results if not r[0]]
    width = max(len(r[1]) for r in results) + 2
    print("=" * (width + 30))
    print("离线冒烟测试")
    print("=" * (width + 30))
    for ok, name, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name:<{width}} {detail}")
    print("-" * (width + 30))
    print(f"  通过 {len(results) - len(failed)}/{len(results)}")
    if failed:
        print("\n  有失败项，请先修复再继续。")
        return 1
    print("  全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
