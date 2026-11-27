"""撞稿预检：发布/下载前搜各目标平台看是否已有高度相似的稿件。

支持外部搜索的平台注册表 SEARCHABLE：
- bilibili: web-interface 搜索 API(需 SESSDATA 防风控)
- youtube: yt-dlp ytsearch 公开搜索(免登录)
抖音/快手/小红书/视频号无公开搜索 API——靠本地防线兜底：
UNIQUE(source_id) + 内容指纹 + 发布前"与已发布稿相似标题"拦截(全平台生效)。
"""
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
SEARCHABLE = {"bilibili", "youtube"}        # 有可用公开搜索的平台
_TAG_RE = re.compile(r"<[^>]+>")
_NON_WORD = re.compile(r"[^0-9a-z一-鿿]+")


def _norm(s: str) -> str:
    return _NON_WORD.sub("", (s or "").lower())


def _bigrams(s: str) -> set:
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def _sim(a: str, b: str) -> float:
    """标题相似度: 编辑距离 ∪ 字符bigram Jaccard 取大。
    前者抓改写, 后者抓语序重排("全过程X" vs "X全过程")。"""
    if not (a and b):
        return 0.0
    from difflib import SequenceMatcher
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    edit = SequenceMatcher(None, na, nb).ratio()
    A, B = _bigrams(na), _bigrams(nb)
    jac = len(A & B) / len(A | B)
    return max(edit, jac)


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


def _search_bilibili(cfg: dict, queries: list, topk: int,
                     own_mid: int, own_bvids: set) -> dict | None:
    """B站搜索后端: 同账号已发稿排除。"""
    best = None
    for q in queries:
        try:
            r = httpx.get(SEARCH_URL, headers=missions._headers(cfg), timeout=15,
                          params={"search_type": "video", "keyword": q, "page": 1})
            data = r.json()
        except Exception as e:
            log.warning("precheck bilibili query failed: %s", e)
            continue
        if data.get("code") not in (0, None):
            log.warning("precheck bilibili api code=%s", data.get("code"))
            continue
        results = (data.get("data") or {}).get("result") or []
        for v in results[:topk]:
            if v.get("bvid") in own_bvids or (own_mid and v.get("mid") == own_mid):
                continue
            v_title = _TAG_RE.sub("", v.get("title") or "")
            sim = max(_sim(v_title, queries[0]), _sim(v_title, queries[-1]))
            if best is None or sim > best["sim"]:
                best = {"bvid": v.get("bvid", ""), "title": v_title,
                        "author": v.get("author", ""), "sim": sim}
        time.sleep(1)  # 防风控
    return best


def _search_youtube(cfg: dict, queries: list, topk: int,
                    own_mid: int, own_bvids: set) -> dict | None:
    """YouTube 搜索后端: yt-dlp ytsearch 公开搜索, 免登录。"""
    from yt_dlp import YoutubeDL
    best = None
    for q in queries:
        try:
            with YoutubeDL({"quiet": True, "no_warnings": True,
                            "extract_flat": True, "noplaylist": True}) as y:
                data = y.extract_info(f"ytsearch{topk}:{q}", download=False) or {}
        except Exception as e:
            log.warning("precheck youtube query failed: %s", e)
            continue
        for v in (data.get("entries") or [])[:topk]:
            t = v.get("title") or ""
            sim = max(_sim(t, queries[0]), _sim(t, queries[-1]))
            if best is None or sim > best["sim"]:
                best = {"bvid": v.get("id") or v.get("url", ""),
                        "title": t,
                        "author": v.get("channel") or v.get("uploader", ""),
                        "sim": sim}
        time.sleep(1)
    return best


_BACKENDS = {"bilibili": _search_bilibili, "youtube": _search_youtube}


def check_collision(cfg: dict, item: dict, topk: int = 8,
                    threshold: float = 0.72, db=None,
                    targets: set | None = None) -> dict | None:
    """按条目目标平台逐平台搜相似稿，命中返回
    {platform,bvid,title,author,sim}。自己已发的稿不算撞稿。
    targets: 实际要投递的平台集; None=按启用平台自动判定。
    B站登录态在时始终参与(中文最大公开语料, 作全平台代理撞稿探测)。
    无公开搜索的平台(抖音/快手/小红书/视频号)不搜——由本地防线兜底。"""
    queries = [q for q in (item.get("title"), item.get("orig_title")) if q]
    if not queries:
        return None
    queries = [queries[0], queries[-1]]      # 生成标题+原标题两个探测词
    if targets is None:
        from . import multipub
        targets = {"bilibili"} | set(multipub.enabled_platforms(cfg))
    if missions._sessdata(cfg):
        targets = set(targets) | {"bilibili"}
    own_mid = _own_mid(cfg)
    own_bvids = {i["bvid"] for i in (db.list_items(status="published", limit=2000)
                                     if db else [])}
    best, best_p = None, ""
    for p in sorted(set(targets) & SEARCHABLE):
        hit = _BACKENDS[p](cfg, queries, topk, own_mid, own_bvids)
        if hit and (best is None or hit["sim"] > best["sim"]):
            best, best_p = hit, p
    if best and best["sim"] >= threshold:
        best["platform"] = best_p
        return best
    return None
