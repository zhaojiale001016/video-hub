"""B站投稿活动(mission)自动匹配。

原理: 投稿页"参与活动"对应 mission_id，投稿时带上即自动参加活动
(biliup-rs --mission-id)。活动列表从 member.bilibili.com 拉取，
需要登录 cookie(biliup 登录后的 cookies.json)。
"""
import json
import logging
from pathlib import Path

import httpx

from . import config as C  # noqa: F401  (供下游函数使用)

log = logging.getLogger("missions")

# 投稿页"参与活动/话题"数据源: 话题列表(v2分页更大)+话题搜索
TOPIC_LIST_URL = "https://member.bilibili.com/x/vupre/web/topic/type/v2"
TOPIC_SEARCH_URL = "https://member.bilibili.com/x/vupre/web/topic/search"

_cache: dict = {"ts": 0, "list": []}


def _sessdata(cfg: dict) -> str | None:
    """从 biliup cookies.json 里提取 SESSDATA（兼容不同结构）。"""
    p = Path(C.abs_path(cfg, C.get_setting(cfg, "biliup_cookie", "data/cookies.json")))
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if cur.get("name") == "SESSDATA" and cur.get("value"):
                return cur["value"]
            stack += list(cur.values())
        elif isinstance(cur, list):
            stack += cur
    return None


def _cookie_str(cfg: dict) -> str:
    """从 cookies.json 拼完整 Cookie 头。"""
    p = Path(C.abs_path(cfg, C.get_setting(cfg, "biliup_cookie", "data/cookies.json")))
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ""
    out = {}

    def walk(n):
        if isinstance(n, dict):
            if n.get("name") and n.get("value"):
                out[n["name"]] = n["value"]
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(data)
    return "; ".join(f"{k}={v}" for k, v in out.items())


_buvid_cache: dict = {"buvid3": ""}


def _buvid3() -> str:
    """搜类接口需要 buvid3 防412，用finger/spi领一个缓存着。"""
    if _buvid_cache["buvid3"]:
        return _buvid_cache["buvid3"]
    try:
        r = httpx.get("https://api.bilibili.com/x/frontend/finger/spi",
                      timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        b = (r.json().get("data") or {}).get("b_3") or ""
        _buvid_cache["buvid3"] = b
    except Exception:
        pass
    return _buvid_cache["buvid3"]


def _headers(cfg: dict) -> dict:
    cookie = _cookie_str(cfg)
    if "buvid3" not in cookie and (b := _buvid3()):
        cookie = f"buvid3={b}; " + cookie
    return {"Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Referer": "https://member.bilibili.com/platform/upload/video/frame"}


def _norm(t: dict) -> dict:
    return {
        "mission_id": t.get("mission_id") or 0,
        "topic_id": t.get("topic_id") or 0,
        "name": t.get("topic_name") or t.get("name") or "",
        "tag": t.get("topic_name") or "",          # 话题投稿按话题名当tag
        "desc": (t.get("activity_description") or t.get("description") or "")[:200],
        "activity_text": t.get("activity_text") or "",
    }


def fetch_missions(cfg: dict, force: bool = False) -> list[dict]:
    """拉取投稿页可选活动/话题(带mission_id的才是真活动)。
    返回 [{mission_id, topic_id, name, tag, desc}]。"""
    import time
    if not force and _cache["list"] and time.time() - _cache["ts"] < 3600:
        return _cache["list"]
    if not _sessdata(cfg):
        log.warning("no SESSDATA, run `cli.py login` first")
        return []
    missions = []
    try:
        for pn in (0, 1):  # 拉两页共800个
            r = httpx.get(TOPIC_LIST_URL, headers=_headers(cfg), timeout=15,
                          params={"pn": pn, "ps": 400})
            topics = (r.json().get("data") or {}).get("topics") or []
            for t in topics:
                m = _norm(t)
                if m["mission_id"] and m["name"]:   # 只有带mission_id的才是活动
                    missions.append(m)
            if len(topics) < 400:
                break
        _cache.update(ts=time.time(), list=missions)
        log.info("fetched %d missions", len(missions))
    except Exception as e:
        log.warning("mission fetch failed: %s", e)
    return missions


def _tid_name(tid: int) -> str:
    """tid→分区名，让"美食分区的视频"能匹配"美食活动"。"""
    try:
        p = C.ROOT / "config" / "tids.json"
        return (json.loads(p.read_text(encoding="utf-8")).get(str(tid)) or "")
    except Exception:
        return ""


def pick_mission(cfg: dict, line: str, item: dict | None = None) -> dict | None:
    """给条目挑活动：关键词必须同时出现在活动名和条目上下文里，
    避免"洗地毯视频跑去参加美食活动"这种错配。"""
    mc = cfg.get("missions", {})   # missions 是顶级配置，不在 settings 下
    if not mc.get("enabled", True):
        return None
    prefs = (mc.get("match") or {}).get(line, [])
    missions = fetch_missions(cfg)
    if not missions:
        return None
    # 条目上下文: 生成标题+标签+品类+分区名+原标题+UP主
    ctx = ""
    if item:
        try:
            tags = json.loads(item.get("tags") or "[]")
        except Exception:
            tags = []
        src = C.get_source(cfg, item.get("source_name") or "") or {}
        ctx = " ".join([item.get("title") or "", " ".join(tags),
                        src.get("category") or "", _tid_name(item.get("tid") or 0),
                        item.get("orig_title") or "", item.get("uploader") or ""])
    best, best_score = None, 0
    for m in missions:
        text = m["name"] + m.get("desc", "")
        score = sum(1 for kw in prefs if kw in text and (not ctx or kw in ctx))
        if score > best_score:
            best, best_score = m, score
    return best
