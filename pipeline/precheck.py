"""撞稿预检：发布前搜B站看是否已有高度相似的稿件。"""
import json
import logging
import re
import time
from pathlib import Path

import httpx

from . import config as C
from . import missions

log = logging.getLogger("precheck")

SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/type"
_TAG_RE = re.compile(r"<[^>]+>")
_NON_WORD = re.compile(r"[^0-9a-z一-鿿]+")


def _norm(s: str) -> str:
    return _NON_WORD.sub("", (s or "").lower())


def _sim(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio() if a and b else 0.0


def _own_mid(cfg: dict) -> int:
    """自己的 mid，用于排除自己已发的稿。"""
    p = Path(C.abs_path(cfg, C.get_setting(cfg, "biliup_cookie", "data/cookies.json")))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        stack = [data]
        while stack:
            n = stack.pop()
            if isinstance(n, dict):
                if n.get("name") == "DedeUserID":
                    return int(n["value"])
                stack += list(n.values())
            elif isinstance(n, list):
                stack += n
    except Exception:
        pass
    return 0


def check_collision(cfg: dict, item: dict, topk: int = 8,
                    threshold: float = 0.72, db=None) -> dict | None:
    """用生成标题+原标题分别搜B站，相似度超阈值返回 {bvid,title,author,sim}。
    自己账号已发的稿不算撞稿。"""
    queries = [q for q in (item.get("title"), item.get("orig_title")) if q]
    if not queries or not missions._sessdata(cfg):
        return None
    own_mid = _own_mid(cfg)
    own_bvids = {i["bvid"] for i in (db.list_items(status="published", limit=2000)
                                     if db else [])}
    best = None
    for q in queries:
        try:
            r = httpx.get(SEARCH_URL, headers=missions._headers(cfg), timeout=15,
                          params={"search_type": "video", "keyword": q, "page": 1})
            data = r.json()
        except Exception as e:
            log.warning("precheck query failed: %s", e)
            continue
        if data.get("code") not in (0, None):
            log.warning("precheck api code=%s", data.get("code"))
            continue
        results = (data.get("data") or {}).get("result") or []
        for v in results[:topk]:
            if v.get("bvid") in own_bvids or (own_mid and v.get("mid") == own_mid):
                continue
            v_title = _TAG_RE.sub("", v.get("title") or "")
            sim = max(_sim(v_title, item.get("title") or ""),
                      _sim(v_title, item.get("orig_title") or ""))
            if best is None or sim > best["sim"]:
                best = {"bvid": v.get("bvid", ""), "title": v_title,
                        "author": v.get("author", ""), "sim": sim}
        time.sleep(1)  # 防风控
    if best and best["sim"] >= threshold:
        return best
    return None
