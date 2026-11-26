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
PLATFORMS = {"kuaishou": "快手", "tencent": "视频号", "douyin": "抖音",
             "xiaohongshu": "小红书", "baijiahao": "百家号", "weibo": "微博",
             "tiktok": "TikTok", "youtube": "YouTube"}
LOGIN_CMD = {"kuaishou": "ks", "tencent": "sph"}   # 便捷别名; 通用命令 login-extra <platform>

# 各平台字段上限(超了被截/失败); 未列出的走默认
_DEFAULT_LIMIT = {"title": 30, "desc": 1000, "tags": 5}
_LIMITS = {"kuaishou": {"title": 30, "desc": 1000, "tags": 5},
           "tencent": {"title": 40, "desc": 1000, "tags": 10}}


def _sau_py(cfg: dict) -> str:
    return str(Path(C.abs_path(cfg, SAU_DIR)) / ".venv" / "bin" / "python")


def _account(cfg: dict, platform: str) -> str:
    return ((cfg.get("extra_platforms") or {}).get(platform) or {}).get("account", "main")


def enabled_platforms(cfg: dict) -> list[str]:
    """extra_platforms 里 enabled 的全部平台(不限制于内置两平台,
    sau 支持的任何平台 id 均可配)。"""
    ep = cfg.get("extra_platforms") or {}
    return [p for p, c in ep.items() if (c or {}).get("enabled")]


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
           "--desc", desc, "--tags", ",".join(tags[:lim["tags"]])]
    if item.get("cover_path"):
        # 视频号主封面参数要3:4竖图, 我们的16:9横图走 landscape 专用参数
        cmd += ["--thumbnail-landscape" if platform == "tencent"
                else "--thumbnail", item["cover_path"]]
    # B站定时发布→其他平台也定时, 避免一边预约一边先发了
    dtime = item.get("dtime") or 0
    if dtime and dtime > _t.time() + 600:
        cmd += ["--schedule", _t.strftime("%Y-%m-%d %H:%M", _t.localtime(dtime))]
    try:
        progress.set_stage(iid, f"{name}投递中", None, "浏览器自动化投稿")
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=str(sau), timeout=timeout)
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
