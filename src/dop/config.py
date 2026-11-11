"""配置读写：带默认值的深度合并，支持点号路径 get/set。"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from . import paths
from .util import atomic_write_json, get_logger, read_json

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    # ---- 每天几点换 ----
    "schedule": {
        "time": "08:30",        # 24 小时制，如 "07:15"
        "catch_up": True,       # 开机时如果发现今天还没换，立刻补换
        "paused": False,        # 暂停自动更换
    },
    # ---- 作品年代范围（含端点）----
    "date_range": [1300, 1800],
    # ---- 图源 ----
    "sources": {
        "met": {
            "enabled": True,
            "weight": 1.0,
            # 11 = 欧洲绘画，15 = 罗伯特·雷曼收藏（都是欧洲油画主库）
            "departments": [11, 15],
            "target_valid": 300,           # 想收集多少幅有效作品
            "max_attempts": 700,           # 最多查多少条明细
        },
        "artic": {"enabled": True, "weight": 1.0},
        "cleveland": {"enabled": True, "weight": 0.8},
        # 哈佛艺术博物馆：需要免费 API Key
        #   申请：https://harvardartmuseums.org/collections/api
        #   Key 存放优先级：环境变量 DOP_HARVARD_API_KEY / HARVARD_API_KEY > config.json
        #   注意其使用条款：内容缓存不得超过两周、每天不超过 2500 次调用、
        #   必须回链、仅限非商业用途。
        "harvard": {
            "enabled": True,
            "weight": 0.7,
            "api_key": "",          # 留空即可用环境变量；也可填在这里（文件不在代码仓库内）
            "cache_days": 14,       # 条款上限就是 14 天，调大无效
            "target_valid": 400,    # 想要多少幅有效作品
            "max_pages": 12,        # 每页 100 条，12 页足够覆盖欧洲绘画
        },
    },
    # ---- 选图策略 ----
    "selection": {
        "recency_days": 540,        # 这么多天内出现过的作品不再重复
        "weighted_by_source": True, # 按图源权重抽取
        "landscape_only": True,     # 横屏使用：排除高度大于宽度的竖图
    },
    # ---- 图像处理 ----
    "image": {
        # fit = 完整显示整幅画（推荐，竖构图油画不会被裁切）
        # fill = 铺满屏幕（会裁掉画面边缘）；center = 原尺寸居中不放大
        "style": "fit",             # fit | fill | center | auto
        # 多屏布局：
        #   each    = 每块屏各显示一份完整版式（推荐，不受主副屏识别影响）
        #   primary = 画作只在主屏，其它屏是模糊延伸
        #   span    = 一整张跨屏图切分
        "multi_monitor": "each",
        "background": "blur",       # blur | color（fill/center 之外的留白底色）
        "background_color": "#0d0d12",
        "blur_strength": 42,
        "darken": 0.55,             # 留白背景的压暗程度 0~0.9
        "format": "jpeg",           # jpeg | png
        "jpeg_quality": 93,
        "show_caption": True,       # 左下角显示作品信息
        "show_museum": True,
        "max_download_mb": 80,      # 单张图片下载上限
    },
    # ---- 高级 ----
    "advanced": {
        "catalog_ttl_days": 30,     # 目录缓存有效期
        "request_timeout": 60,
        # Met 对高频请求敏感。官方限 80 次/秒，实测 8 线程猛刷会被封；
        # 8 次/秒 + 4 线程既安全又能让全量重建控制在 2-3 分钟。
        "concurrency": 4,
        "requests_per_second": 8,
        # 每隔多少秒确认一次桌面壁纸还是本程序设的那张（被别的程序改掉会设回去）。
        # 设成 0 关闭这个守护；暂停自动更换时也不会干预。
        "wallpaper_guard_seconds": 300,
        "max_download_attempts": 4,  # 单次更换最多试几幅作品
        "keep_archive": False,      # 是否按日期归档每天的壁纸
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


SECRET_HINTS = ("api_key", "apikey", "token", "secret", "password")


def is_secret_path(dotted: str) -> bool:
    low = str(dotted).lower()
    return any(hint in low for hint in SECRET_HINTS)


def mask(value: Any) -> str:
    """只显示密钥前 4 位并附加星号，便于确认配置是否正确。"""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "****"
    return f"{text[:4]}****（共 {len(text)} 位）"


def mask_secrets(cfg: dict) -> dict:
    """返回一份把密钥打码后的副本，用于界面展示。"""
    out: dict[str, Any] = {}
    for key, value in (cfg or {}).items():
        if isinstance(value, dict):
            out[key] = mask_secrets(value)
        elif is_secret_path(key) and value:
            out[key] = mask(value)
        else:
            out[key] = value
    return out


def env_api_key() -> str:
    """从环境变量读取哈佛 Key（优先级高于配置文件）。"""
    for name in ("DOP_HARVARD_API_KEY", "HARVARD_API_KEY", "HARVARD_APIKEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = Path(path) if path else paths.config_path()
    raw = read_json(path, {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    cfg = _deep_merge(DEFAULT_CONFIG, raw)
    # 环境变量优先：这样可以把 Key 完全放在系统环境里，配置文件里什么都不留
    override = env_api_key()
    if override:
        cfg.setdefault("sources", {}).setdefault("harvard", {})["api_key"] = override
    return cfg


def save_config(cfg: dict[str, Any], path: Path | None = None) -> None:
    path = Path(path) if path else paths.config_path()
    data = copy.deepcopy(cfg)
    # 由环境变量提供的 Key 不落盘：这样"只放环境变量"就真的是只在环境里
    if env_api_key():
        harvard = (data.get("sources") or {}).get("harvard")
        if isinstance(harvard, dict):
            harvard["api_key"] = ""
    atomic_write_json(path, data)
    get_logger("config").debug("配置已保存到 %s", path)


def get_value(cfg: dict, dotted: str, default: Any = None) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_value(cfg: dict, dotted: str, value: Any) -> Any:
    """按点号路径写入，并返回被强制转换后的值。"""
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return value


def coerce(text: str) -> Any:
    """把命令行字符串转成合适的 Python 类型。"""
    low = str(text).strip()
    if low.lower() in ("true", "yes", "on", "是"):
        return True
    if low.lower() in ("false", "no", "off", "否"):
        return False
    if low.lower() in ("null", "none"):
        return None
    try:
        return int(low)
    except ValueError:
        pass
    try:
        return float(low)
    except ValueError:
        pass
    if low.startswith("[") or low.startswith("{"):
        import json

        try:
            return json.loads(low)
        except ValueError:
            pass
    return text
