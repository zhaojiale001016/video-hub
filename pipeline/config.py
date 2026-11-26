"""加载 config/sources.yaml + config/overrides.json 覆盖层，提供统一访问入口。"""
import json
import os
import threading
from pathlib import Path

import yaml

_ov_lock = threading.Lock()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "sources.yaml"
OVERRIDES = ROOT / "config" / "overrides.json"


def _deep_merge(base: dict, over: dict):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def load_overrides() -> dict:
    try:
        return json.loads(OVERRIDES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_overrides(over: dict):
    """原子写: 先落 tmp 再 rename, 进程中断不会截坏 json。"""
    tmp = OVERRIDES.with_suffix(".tmp")
    tmp.write_text(json.dumps(over, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(OVERRIDES)


def update_overrides(mutate) -> dict:
    """load→mutate→save 全程持锁, 防并发请求互相丢更新。返回最终 overrides。"""
    with _ov_lock:
        over = load_overrides()
        mutate(over)
        save_overrides(over)
        return over


def apply_overrides(cfg: dict) -> dict:
    """UI 可编辑层: overrides.json 覆盖 yaml。源列表存在则整体替换。"""
    over = load_overrides()
    for sec in ("settings", "subtitles", "llm", "blacklist",
                "extra_platforms", "missions"):
        if isinstance(over.get(sec), dict):
            _deep_merge(cfg.setdefault(sec, {}), over[sec])
    if isinstance(over.get("sources"), list):
        cfg["sources"] = over["sources"]
    return cfg


def load_config(path: str | None = None) -> dict:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        # 首启: sources.yaml 是本地私有文件, 从示例复制一份再改
        example = cfg_path.with_name("sources.example.yaml")
        if example.exists():
            import shutil
            shutil.copy(example, cfg_path)
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg["_root"] = str(ROOT)
    return apply_overrides(cfg)


def abs_path(cfg: dict, p: str) -> str:
    """配置里的相对路径一律相对项目根目录解析。"""
    path = Path(p)
    return str(path if path.is_absolute() else ROOT / p)


def settings(cfg: dict) -> dict:
    return cfg.get("settings") or {}


def get_setting(cfg: dict, key: str, default=None):
    return (cfg.get("settings") or {}).get(key, default)


def proxy(cfg: dict) -> str | None:
    """settings.proxy > https_proxy 环境变量。"""
    p = get_setting(cfg, "proxy") or os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    return p or None


def enabled_sources(cfg: dict) -> list[dict]:
    return [s for s in (cfg.get("sources") or []) if s.get("enabled", True)]


def get_source(cfg: dict, name: str) -> dict | None:
    for s in (cfg.get("sources") or []):
        if s.get("name") == name:
            return s
    return None


def blacklist_uploaders(cfg: dict) -> list[str]:
    return (cfg.get("blacklist") or {}).get("uploaders", []) or []


def blacklist_keywords(cfg: dict) -> list[str]:
    return cfg.get("blacklist", {}).get("keywords", []) or []
