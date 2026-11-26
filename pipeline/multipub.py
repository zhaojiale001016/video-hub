"""多平台投递: 通过 vendor/sau(social-auto-upload)的 CLI 子进程发布到快手/视频号。

sau 用 patchright 浏览器自动化模拟投稿页，登录态存 cookies/<platform>_<account>.json。
首次需 `cli.py login-ks` / `cli.py login-sph` 扫码(开可视浏览器)。
注意: 这两平台无"转载"声明位, 简介已自动带源链接做标注。
"""
import json
import logging
import subprocess
from pathlib import Path

from . import config as C

log = logging.getLogger("multipub")

SAU_DIR = "vendor/sau"
PLATFORMS = {"kuaishou": "快手", "tencent": "视频号"}
LOGIN_CMD = {"kuaishou": "ks", "tencent": "sph"}   # cli.py login-ks / login-sph

# 各平台字段上限(超了被截/失败)
_LIMITS = {"kuaishou": {"title": 30, "desc": 1000, "tags": 5},
           "tencent": {"title": 40, "desc": 1000, "tags": 10}}


def _sau_py(cfg: dict) -> str:
    return str(Path(C.abs_path(cfg, SAU_DIR)) / ".venv" / "bin" / "python")


def _account(cfg: dict, platform: str) -> str:
    return ((cfg.get("extra_platforms") or {}).get(platform) or {}).get("account", "main")


def enabled_platforms(cfg: dict) -> list[str]:
    ep = cfg.get("extra_platforms") or {}
    return [p for p in PLATFORMS if (ep.get(p) or {}).get("enabled")]


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
    lim = _LIMITS[platform]
    name = PLATFORMS[platform]
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


def publish_all(item: dict, cfg: dict) -> list[dict]:
    """对启用平台逐一投递; 条目级 pub_platforms 可圈定子集(""=全部, "[]"=只发B站)。"""
    allow = None
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
            results.append({"platform": p, "name": PLATFORMS[p], "ok": False,
                            "msg": f"未登录, 跑 cli.py login-{LOGIN_CMD.get(p, p[:2])}"})
            continue
        results.append(publish_to(p, item, cfg))
    return results


def login_cmd(platform: str, cfg: dict) -> tuple[list[str], str]:
    """返回扫码登录命令(sau login --headed)和工作目录。"""
    sau = Path(C.abs_path(cfg, SAU_DIR))
    cmd = [_sau_py(cfg), str(sau / "sau_cli.py"), platform, "login",
           "--account", _account(cfg, platform), "--headed"]
    return cmd, str(sau)
