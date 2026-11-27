"""多平台投递: 通过 vendor/sau(social-auto-upload)的 CLI 子进程发布到各平台。

sau 用 patchright 浏览器自动化模拟投稿页，登录态存 cookies/<platform>_<account>.json。
extra_platforms 里 enabled 的平台都会被投递; 支持 sau 全平台
(快手/视频号/抖音/小红书/百家号/微博/TikTok 等, 见 vendor/sau/sau_cli.py)。
首次需 `cli.py login-extra <平台id>` 扫码(开可视浏览器; ks/sph 为便捷别名)。
注意: sau 系平台无"转载"声明位, 简介已自动带源链接做标注。
"""
import json
import logging
import subprocess
from pathlib import Path

from . import config as C

log = logging.getLogger("multipub")

SAU_DIR = "vendor/sau"
# 平台显示名(已知的映射中文名; 未列出的 sau 平台直接用 id 展示)。
# sau 实际支持: douyin/xiaohongshu/bilibili/tencent/kuaishou/weibo/
# baijiahao/tiktok(以 sau_cli.py PLATFORMS 为准), extra_platforms 任意加。
# sau_cli.PLATFORMS 全部视频平台(去掉 article/facebook/xueqiu 纯图文、
# bilibili 由内置 biliup 走)。extra_platforms 可勾选其中任意平台;
# 未列出的自定义 id 也允许(按 sau 实际支持)。
SAU_PLATFORMS = [
    "kuaishou", "tencent", "douyin", "xiaohongshu", "baijiahao",
    "weibo", "youtube", "facebook-page", "instagram",
    "alipay", "hupu", "dayu", "zhihu",
]
PLATFORMS = {"kuaishou": "快手", "tencent": "视频号", "douyin": "抖音",
             "xiaohongshu": "小红书", "baijiahao": "百家号", "weibo": "微博",
             "youtube": "YouTube", "facebook-page": "Facebook主页",
             "instagram": "Instagram", "alipay": "支付宝", "hupu": "虎扑",
             "dayu": "大鱼号", "zhihu": "知乎"}
_RESERVED = {"bilibili", "article"}   # 不允许进 extra_platforms
_PLATFORM_ID_RE = __import__("re").compile(r"[a-z][a-z0-9_-]{0,19}")


def valid_platform_id(pid: str) -> str | None:
    """平台 id 合法性: 返回错误原因或 None。
    强制 slug(会拼进 cookie 文件名/subprocess 参数)且非保留字。"""
    if not isinstance(pid, str) or not _PLATFORM_ID_RE.fullmatch(pid):
        return "平台id须为小写slug([a-z][a-z0-9_-]{0,19})"
    if pid in _RESERVED:
        return f"{pid} 为保留平台id, 不可配在 extra_platforms"
    return None

# sau 各平台 upload-video 可选参数支持矩阵(见 vendor/sau/sau_commands/*.py):
_NO_THUMB = {"zhihu", "facebook-page"}   # 不支持 --thumbnail
_NO_TAGS = {"zhihu"}                     # 不支持 --tags
_LAND_THUMB = {"tencent", "douyin"}      # 支持 --thumbnail-landscape 横版位 + --thumbnail-portrait 竖版位
_PORTRAIT_THUMB = {"kuaishou", "xiaohongshu"}  # 竖版信息流: 有 3:4 竖封时优先喂竖封
_NO_SCHEDULE = {"weibo", "instagram", "hupu",
                "baijiahao", "alipay"}   # 不支持 --schedule
LOGIN_CMD = {"kuaishou": "ks", "tencent": "sph"}   # 便捷别名; 通用命令 login-extra <platform>

# 各平台字段上限(超了被截/失败); 未列出的走默认
_DEFAULT_LIMIT = {"title": 30, "desc": 1000, "tags": 5}
_LIMITS = {"kuaishou": {"title": 30, "desc": 1000, "tags": 5},
           "tencent": {"title": 40, "desc": 1000, "tags": 10}}


def _sau_py(cfg: dict) -> str:
    import sys
    sub = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    return str(Path(C.abs_path(cfg, SAU_DIR)) / ".venv" / sub)


def _account(cfg: dict, platform: str) -> str:
    return ((cfg.get("extra_platforms") or {}).get(platform) or {}).get("account", "main")


def enabled_platforms(cfg: dict) -> list[str]:
    """extra_platforms 里 enabled 的全部平台(不限制于内置两平台,
    sau 支持的任何平台 id 均可配)。"""
    ep = cfg.get("extra_platforms") or {}
    return [p for p, c in ep.items()
            if (c or {}).get("enabled") and p not in _RESERVED]


def logged_in(cfg: dict, platform: str) -> bool:
    """该平台 cookie 文件是否存在(不验证有效性, sau 发布时自验)。"""
    sau = Path(C.abs_path(cfg, SAU_DIR))
    return any(sau.glob(f"cookies/{platform}_{_account(cfg, platform)}*.json")) or \
        any(sau.glob(f"cookies/{platform}/{_account(cfg, platform)}*.json"))


def publish_to(platform: str, item: dict, cfg: dict, timeout: int = 1800) -> dict:
    """发布一条到指定平台，返回 {platform,name,ok,msg}。失败不抛异常。"""
    import time as _t
    from . import progress
    sau = Path(C.abs_path(cfg, SAU_DIR))
    lim = _LIMITS.get(platform, _DEFAULT_LIMIT)
    name = PLATFORMS.get(platform, platform)
    iid = item.get("id") or 0
    title = (item.get("title") or item.get("orig_title") or "")[:lim["title"]]
    tags = item.get("tags") or []
    if isinstance(tags, str):
        tags = json.loads(tags)
    desc = (item.get("description") or "")[:lim["desc"]]
    cmd = [_sau_py(cfg), str(sau / "sau_cli.py"), platform, "upload-video",
           "--account", _account(cfg, platform),
           "--file", item["file_path"], "--title", title,
           "--desc", desc]
    # 有头模式: 风控严格平台(抖音等)需人工过验证码。extra_platforms.<p>.headed=true
    # 或服务级 sau_headed_platforms 名单 → 浏览器可见可交互
    if ((cfg.get("extra_platforms") or {}).get(platform) or {}).get("headed") \
            or platform in (C.get_setting(cfg, "sau_headed_platforms") or []):
        cmd += ["--headed"]
    if platform not in _NO_TAGS:
        cmd += ["--tags", ",".join(tags[:lim["tags"]])]
    # 自主声明(抖音等): extra_platforms.<p>.declaration="内容由AI生成" 显式才标,
    # 默认不标——误标会误导平台且损害真实视频分发
    decl = ((cfg.get("extra_platforms") or {}).get(platform) or {}).get("declaration")
    if decl:
        cmd += ["--declaration", str(decl)]
    if platform not in _NO_THUMB:
        cover = item.get("cover_path") or ""
        portrait = item.get("cover_portrait_path") or ""
        has_portrait = bool(portrait) and Path(portrait).exists()
        if platform in _LAND_THUMB:
            # 视频号/抖音双封面位: 4:3横版走landscape + 3:4竖版走portrait
            if cover:
                cmd += ["--thumbnail-landscape", cover]
            if has_portrait:
                cmd += ["--thumbnail-portrait", portrait]
        elif has_portrait and platform in _PORTRAIT_THUMB:
            cmd += ["--thumbnail", portrait]   # 竖版信息流平台喂 3:4
        elif cover:
            cmd += ["--thumbnail", cover]
    # B站定时发布→其他平台也定时, 避免一边预约一边先发了;
    # 不支持 --schedule 的平台直接即时发
    dtime = item.get("dtime") or 0
    # 分平台错峰: offset_min>0 时该平台的预约时间整体后移,
    # 防同一稿件多平台同刻发布被风控画像成矩阵号
    off = ((cfg.get("extra_platforms") or {}).get(platform) or {}).get("offset_min") or 0
    if off and dtime:
        dtime += int(float(off) * 60)
    if dtime and dtime > _t.time() + 600 and platform not in _NO_SCHEDULE:
        cmd += ["--schedule", _t.strftime("%Y-%m-%d %H:%M", _t.localtime(dtime))]
    env = None
    if platform == "tencent":
        # 视频号「视频标注」: 搬运内容默认不勾AI标注; 设置 tencent_content_label 可指定文案
        import os
        env = dict(os.environ,
                   SAU_TENCENT_CONTENT_LABEL=C.get_setting(
                       cfg, "tencent_content_label", "none") or "none")
    try:
        progress.set_stage(iid, f"{name}投递中", None, "浏览器自动化投稿")
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           cwd=str(sau), timeout=timeout, env=env)
        out = ((r.stdout or "") + "\n" + (r.stderr or ""))[-800:]
        ok = r.returncode == 0
        log.info("multipub %s rc=%s: %s", platform, r.returncode, out[-300:])
        return {"platform": platform, "name": name, "ok": ok, "msg": out}
    except Exception as e:
        log.warning("multipub %s failed: %s", platform, e)
        return {"platform": platform, "name": name, "ok": False, "msg": str(e)[:400]}
    finally:
        progress.set_stage(iid, "收尾中")


def publish_all(item: dict, cfg: dict, allow: set | None = None) -> list[dict]:
    """对启用平台逐一投递; 条目级 pub_platforms 可圈定子集(""=全部, "[]"=只发B站)。
    allow=显式白名单(重试去重: 调用方剔除已成功平台后传入)。"""
    if allow is None:
        sel = (item.get("pub_platforms") or "").strip()
        if sel:
            try:
                allow = set(json.loads(sel))
            except Exception:
                allow = None
    results = []
    for p in enabled_platforms(cfg):
        if allow is not None and p not in allow:
            continue
        if not logged_in(cfg, p):
            results.append({"platform": p, "name": PLATFORMS.get(p, p), "ok": False,
                            "msg": f"未登录, 跑 cli.py login-extra {p}"})
            continue
        results.append(publish_to(p, item, cfg))
    return results


def login_cmd(platform: str, cfg: dict) -> tuple[list[str], str]:
    """返回扫码登录命令(sau login --headed)和工作目录。"""
    sau = Path(C.abs_path(cfg, SAU_DIR))
    cmd = [_sau_py(cfg), str(sau / "sau_cli.py"), platform, "login",
           "--account", _account(cfg, platform), "--headed"]
    return cmd, str(sau)
