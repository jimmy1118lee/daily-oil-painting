"""把当前作品信息渲染成一个本地 HTML 页面（用于"查看详情"）。"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Optional

from . import paths

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title} — 今日名画</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 48px 24px; background: #0e0e12; color: #e8e6e1;
    font-family: "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
  }}
  .wrap {{ max-width: 1020px; margin: 0 auto; }}
  .kicker {{ letter-spacing: .32em; font-size: 12px; color: #b39a5f; text-transform: uppercase; }}
  h1 {{ font-family: Georgia, "Times New Roman", serif; font-size: 34px; margin: 10px 0 6px; line-height: 1.25; }}
  .artist {{ font-size: 18px; color: #c9c5bc; margin-bottom: 4px; }}
  .dated {{ font-size: 15px; color: #8d8a84; }}
  figure {{ margin: 30px 0; }}
  img {{ width: 100%; height: auto; border-radius: 6px; box-shadow: 0 22px 60px rgba(0,0,0,.6); }}
  dl {{ display: grid; grid-template-columns: 108px 1fr; gap: 10px 20px; margin: 28px 0 0; font-size: 15px; }}
  dt {{ color: #8d8a84; }}
  dd {{ margin: 0; }}
  a {{ color: #d9bd76; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .foot {{ margin-top: 34px; padding-top: 18px; border-top: 1px solid #26262e; font-size: 13px; color: #777470; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="kicker">今日名画 · {time_display}</div>
  <h1>{title}</h1>
  <div class="artist">{artist}</div>
  <div class="dated">{dated}</div>
  <figure><img src="{image_src}" alt="{title}"></figure>
  <dl>
    <dt>收藏机构</dt><dd>{museum}</dd>
    <dt>创作年代</dt><dd>{dated}</dd>
    <dt>材质</dt><dd>{medium}</dd>
    <dt>版权</dt><dd>{license}</dd>
    <dt>壁纸文件</dt><dd>{wallpaper_file}</dd>
    <dt>原始图片</dt><dd>{source_file}</dd>
    <dt>馆藏页面</dt><dd>{page_link}</dd>
  </dl>
  <div class="foot">
    由「每日欧洲油画壁纸」生成 · 画面分辨率 {resolution} · 显示器 {monitors}
  </div>
</div>
</body>
</html>
"""


def _file_url(path: Optional[str]) -> str:
    if not path:
        return ""
    try:
        return Path(path).resolve().as_uri()
    except (ValueError, OSError):
        return ""


def build_html(record: dict) -> str:
    def esc(value, default: str = "—") -> str:
        text = str(value).strip() if value is not None else ""
        return html.escape(text) if text else default

    dated = esc(record.get("date_display"))
    page = str(record.get("page_url") or "").strip()
    page_link = (
        f'<a href="{html.escape(page)}" target="_blank">{html.escape(page)}</a>'
        if page
        else "—"
    )
    return TEMPLATE.format(
        title=esc(record.get("title")),
        artist=esc(record.get("artist"), "佚名 / 作者不详"),
        dated=dated,
        time_display=esc(record.get("time_display")),
        museum=esc(record.get("museum")),
        medium=esc(record.get("medium")),
        license=esc(record.get("license"), "公共领域"),
        wallpaper_file=esc(record.get("wallpaper_file")),
        source_file=esc(record.get("source_file")),
        page_link=page_link,
        # 用整张跨屏图预览，才能看到完整的设计（多屏时每屏那张只是它的切片）
        image_src=_file_url(record.get("canvas_file") or record.get("wallpaper_file")),
        resolution=esc(record.get("resolution")),
        monitors=esc(record.get("monitors")),
    )


def write_html(record: dict, out_path: Optional[Path] = None) -> Path:
    out = Path(out_path) if out_path else paths.state_dir() / "current.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(record), encoding="utf-8")
    return out
