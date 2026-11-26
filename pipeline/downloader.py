"""yt-dlp 订阅源轮询与视频下载。"""
import glob
import logging
import os
import subprocess
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from . import config as C
from .db import DB

log = logging.getLogger("downloader")


def _base_opts(cfg: dict) -> dict:
    opts = {"quiet": True, "no_warnings": True, "ignoreerrors": True,
            "noplaylist": True, "socket_timeout": 30}
    proxy = C.proxy(cfg)
    if proxy:
        opts["proxy"] = proxy
    cookies = C.get_setting(cfg, "cookies_file")
    if cookies:
        opts["cookiefile"] = C.abs_path(cfg, cookies)
    return opts


def _entry_url(e: dict) -> str:
    """从 flat entry 提取可访问的原视频链接。"""
    for k in ("webpage_url", "url", "original_url"):
        u = e.get(k)
        if u and str(u).startswith("http"):
            return u
    vid = e.get("id")
    if vid and "youtube" in (e.get("ie_key") or "").lower():
        return f"https://www.youtube.com/watch?v={vid}"
    return e.get("url") or ""


def _blocked(e: dict, cfg: dict, src: dict) -> str | None:
    """命中黑名单/关键词/时长过滤时返回原因，否则 None。"""
    title = (e.get("title") or "").lower()
    uploader = (e.get("uploader") or e.get("channel") or "").lower()
    for bad in C.blacklist_uploaders(cfg):
        if bad.lower() in uploader:
            return f"屏蔽UP主: {bad}"
    for kw in C.blacklist_keywords(cfg) + (src.get("keyword_blacklist") or []):
        if kw.lower() in title:
            return f"屏蔽关键词: {kw}"
    inc = src.get("keyword_include") or []
    if inc and not any(k.lower() in title for k in inc):
        return "未命中包含关键词"
    dur = e.get("duration") or 0
    if dur:
        if src.get("min_duration") and dur < src["min_duration"]:
            return f"时长过短 {dur}s"
        if src.get("max_duration") and dur > src["max_duration"]:
            return f"时长过长 {dur}s"
    return None


def poll_source(src: dict, cfg: dict, db: DB) -> dict:
    """拉取一个订阅源的最新条目，新视频入库。返回统计。"""
    # 每源可自定义 limit(搜索源调小防同类内容刷屏)，再被全局 per_source_poll_cap 封顶
    limit = int(src.get("limit") or C.get_setting(cfg, "poll_limit", 15))
    limit = min(limit, int(C.get_setting(cfg, "per_source_poll_cap", 30) or 30))
    opts = _base_opts(cfg) | {"extract_flat": "in_playlist",
                              "playlistend": limit}
    stats = {"source": src["name"], "found": 0, "new": 0, "skipped": 0, "error": ""}
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(src["url"], download=False)
    except DownloadError as e:
        stats["error"] = str(e)[:300]
        log.warning("poll %s failed: %s", src["name"], e)
        return stats
    if not info:
        stats["error"] = "empty result"
        return stats

    entries = list(info.get("entries") or [])
    stats["found"] = len(entries)
    platform = (info.get("extractor") or "").split(":")[0]
    for e in entries:
        if not e:
            continue
        vid = str(e.get("id") or "")
        if not vid:
            continue
        reason = _blocked(e, cfg, src)
        if reason:
            stats["skipped"] += 1
            continue
        item = {
            "source_name": src["name"], "line": src.get("line", ""),
            "platform": platform or (e.get("ie_key") or "").lower(),
            "source_id": vid, "source_url": _entry_url(e),
            "orig_title": e.get("title") or "",
            "orig_desc": (e.get("description") or "")[:2000],
            "uploader": e.get("uploader") or e.get("channel") or info.get("uploader") or "",
            "duration": e.get("duration") or 0,
            "upload_date": e.get("upload_date") or "",
            "tid": src.get("tid") or 0,
        }
        if db.insert_item(item) is not None:
            stats["new"] += 1
    return stats


def poll_all(cfg: dict, db: DB) -> list[dict]:
    out = []
    for src in C.enabled_sources(cfg):
        out.append(poll_source(src, cfg, db))
    return out


def download_item(item: dict, cfg: dict) -> dict:
    """下载视频+封面+字幕到 data/downloads/<id>/，返回文件路径。"""
    item_dir = Path(C.abs_path(cfg, C.get_setting(cfg, "download_dir"))) / str(item["id"])
    import shutil
    free_gb = shutil.disk_usage(str(item_dir.parent if item_dir.parent.exists() else ".")).free / (1 << 30)
    min_free = float(C.get_setting(cfg, "min_free_gb", 10) or 10)
    if free_gb < min_free:
        raise RuntimeError(f"磁盘剩余 {free_gb:.0f}GB < {min_free}GB，先清理再下")
    item_dir.mkdir(parents=True, exist_ok=True)
    src = C.get_source(cfg, item.get("source_name") or "") or {}
    h = int(src.get("max_height") or C.get_setting(cfg, "max_height", 1080))
    outtmpl = str(item_dir / "video.%(ext)s")
    maxd = src.get("max_duration")
    def _yt_prog(d):
        if d.get("status") != "downloading":
            return
        from . import progress as prog
        tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        dl = d.get("downloaded_bytes") or 0
        spd = (d.get("speed") or 0) / 2 ** 20
        if tot:
            prog.set_stage(item["id"], "下载中", dl / tot,
                           f"{dl/2**20:.0f}/{tot/2**20:.0f}MB {spd:.1f}MB/s")
        elif dl:
            prog.set_stage(item["id"], "下载中", None, f"{dl/2**20:.0f}MB")
    opts = _base_opts(cfg) | {
        "ignoreerrors": False,   # 下载报错要抛出来，不能吞成"no video file"
        "format": (f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/"
                   f"bv*[height<={h}]+ba/b[height<={h}]/b"),
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "writethumbnail": True,
        "writesubtitles": True,
        "writeautomaticsub": False,
        "subtitleslangs": ["zh.*", "en.*", "en"],
        "download_archive": C.abs_path(cfg, C.get_setting(cfg, "archive_file")),
        "retries": 3,
        "fragment_retries": 3,
        "noplaylist": True,
        "progress_hooks": [_yt_prog],
    }
    mind = src.get("min_duration")
    if maxd or mind:  # flat阶段缺时长时，完整解析阶段再拦一遍长短(未下载前生效)
        def _dur_filter(info):
            dur = info.get("duration") or 0
            if not dur:
                return None
            if maxd and dur > maxd:
                return f"时长超限 {int(dur)}s"
            if mind and dur < mind:
                return f"时长不足 {int(dur)}s"
            return None
        opts["match_filter"] = _dur_filter
    url = item["source_url"]
    if not url:
        raise RuntimeError("no source_url")
    with YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = {"file_path": "", "cover_path": "", "subtitle_path": ""}
    for ext in ("mp4", "mkv", "webm", "mov"):
        hits = sorted(glob.glob(str(item_dir / f"video.{ext}")))
        if hits:
            files["file_path"] = hits[0]
            break
    for ext in ("jpg", "jpeg", "png", "webp"):
        hits = sorted(glob.glob(str(item_dir / f"video.{ext}")))
        if hits:
            cover = hits[0]
            if cover.endswith(".webp"):  # B站封面不认webp，转jpg
                jpg = cover[:-5] + ".jpg"
                r = subprocess.run(["ffmpeg", "-y", "-i", cover, jpg],
                                   capture_output=True)
                if r.returncode == 0:
                    os.remove(cover)
                    cover = jpg
            files["cover_path"] = cover
            break
    subs = sorted(glob.glob(str(item_dir / "video.*.srt")) +
                  glob.glob(str(item_dir / "video.*.vtt")))
    if subs:
        # 优先中文字幕
        zh = [s for s in subs if ".zh" in os.path.basename(s)]
        files["subtitle_path"] = (zh or subs)[0]
    if not files["file_path"]:
        raise RuntimeError("download produced no video file")
    return files


def cleanup_stale(cfg: dict, db: DB, days: int = 7) -> dict:
    """清理无效条目的本地文件: 孤儿目录 / skipped / failed 且超 days 天未动。"""
    import shutil
    dl = Path(C.abs_path(cfg, C.get_setting(cfg, "download_dir")))
    cut = __import__("time").time() - days * 86400
    freed, removed = 0, []
    if dl.exists():
        for d in dl.iterdir():
            if not d.is_dir() or not d.name.isdigit():
                continue
            it = db.get(int(d.name))
            stale = it is None or (it["status"] in ("skipped", "failed")
                                   and d.stat().st_mtime < cut)
            if not stale:
                continue
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
            removed.append(int(d.name))
    return {"freed_gb": round(freed / (1 << 30), 2), "removed": removed}


def process_new_items(cfg: dict, db: DB, metagen=None, limit: int | None = None,
                      progress=None, item_ids: list[int] | None = None,
                      ignore_cap: bool = False) -> list[dict]:
    """下载 status=new 条目并生成元数据。limit=None 处理整队。
    item_ids 指定条目(手动触发)；daily_download_limit 限每日自动下载量，
    ignore_cap=True 时不受额度限制(手动单条下载用)。
    progress(msg) 可选回调，逐条汇报进度。"""
    import time
    from . import metagen as mg  # 延迟导入避免环
    gen = metagen or mg.gen_meta
    if item_ids:
        items = [it for i in item_ids
                 if (it := db.get(i)) and it.get("status") in ("new", "downloaded")]
    else:
        # downloaded=上次中途被杀的半成品(文件在但没字幕/元数据)，一并捞回来续跑
        items = sorted(
            (it for s in ("new", "downloaded")
             for it in db.list_items(status=s, limit=limit or 10 ** 6)),
            key=lambda x: -x["id"])
    cap = int(C.get_setting(cfg, "daily_download_limit", 0) or 0)
    results = []
    for idx, item in enumerate(items, 1):
        if cap > 0 and not ignore_cap and db.downloaded_today() >= cap:
            msg = f"今日下载额度用完 {db.downloaded_today()}/{cap}，剩余明天自动续跑"
            if progress:
                progress(msg)
            results.append({"id": None, "ok": False, "error": msg})
            break
        try:
            if progress:
                progress(f"[{idx}/{len(items)}] 下载 #{item['id']} {item['orig_title'][:40]}")
            # 撞稿预检前移: 下载前先搜B站，高相似直接跳过，省下载+转写+烧录
            if (C.get_setting(cfg, "precheck_before_download", True)
                    and not (item.get("file_path") and Path(item["file_path"]).exists())):
                from . import precheck, progress as prog
                prog.set_stage(item["id"], "撞稿预检中")
                hit = precheck.check_collision(cfg, item, db=db, threshold=0.85)
                if hit:
                    db.set_status(item["id"], "skipped",
                                  fail_reason=(f"撞稿(相似度{hit['sim']:.0%}): {hit['bvid']} "
                                               f"《{hit['title']}》@{hit['author']} —— 下载前自动跳过"))
                    msg = f"#{item['id']} 撞稿跳过({hit['sim']:.0%}): {hit['bvid']}"
                    results.append({"id": item["id"], "ok": False, "error": msg})
                    if progress:
                        progress(msg)
                    continue
            if not (item.get("file_path") and Path(item["file_path"]).exists()):
                files = download_item(item, cfg)
                db.update(item["id"], status="downloaded",
                          downloaded_at=int(time.time()), **files)
            elif item.get("status") == "new":   # 文件在但还标new(异常情况)，补状态
                db.update(item["id"], status="downloaded")
            # 中文字幕(可选): 翻译/转写+烧录，可能替换 file_path
            from . import subtitle
            sub_fields = subtitle.ensure_zh_subtitle(db.get(item["id"]), cfg)
            if sub_fields:
                db.update(item["id"], **sub_fields)
            from . import progress as prog
            prog.set_stage(item["id"], "生成标题中")
            meta = gen(db.get(item["id"]), cfg, db)  # 重取: 字幕步骤可能改了file_path/subtitle_path
            db.set_meta(item["id"], meta["title"], meta["desc"], meta["tags"], meta["tid"])
            db.set_status(item["id"], "ready")
            msg = f"#{item['id']} 完成 → {meta['title'][:45]}"
            results.append({"id": item["id"], "ok": True, "title": meta["title"]})
        except Exception as e:
            db.set_status(item["id"], "failed", fail_reason=str(e)[:500])
            msg = f"#{item['id']} 失败: {str(e)[:120]}"
            results.append({"id": item["id"], "ok": False, "error": str(e)[:300]})
        finally:
            from . import progress as prog
            prog.clear(item["id"])
        if progress:
            progress(msg)
    return results
