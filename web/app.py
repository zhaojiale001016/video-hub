"""FastAPI 管理台：队列预览 / 元数据编辑 / 一键发布 / 后台任务。"""
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path

import httpx

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import config as C
from pipeline import downloader, uploader, metagen, multipub, progress
from pipeline.db import DB

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("web")

cfg = C.load_config()
db = DB(C.abs_path(cfg, C.get_setting(cfg, "db_file", "data/pipeline.db")))

app = FastAPI(title="Video Hub · 视频搬运中台")

DATA_DIR = Path(C.abs_path(cfg, "data"))
DATA_DIR.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory=str(DATA_DIR)), name="media")
app.mount("/static", StaticFiles(directory=str(ROOT / "web" / "static")), name="static")

# ---------------- 后台任务 ----------------
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _running_job(name: str) -> str | None:
    """同族任务防并发: 'download'/'download:1061' 同族, 'publish-all'/'publish:1' 同族,
    防止批量任务和单条任务同时加工同一文件。"""
    fam = name.split(":")[0].split("-")[0]     # 归一: publish-all→publish
    with JOBS_LOCK:
        for jid, j in JOBS.items():
            if (j["status"] == "running"
                    and j["name"].split(":")[0].split("-")[0] == fam):
                return jid
    return None


def _prune_jobs():
    """JOBS 表常驻内存, 防无界增长: 非 running 任务只留最近200条。"""
    with JOBS_LOCK:
        stale = sorted((k for k, v in JOBS.items() if v["status"] != "running"),
                       key=lambda k: JOBS[k]["ts"])
        for k in stale[:-200]:
            JOBS.pop(k, None)


def run_job(name: str, fn, *args, **kwargs) -> str:
    """同名任务在跑时直接复用其 job_id，防止重复并发。
    check+insert 同锁原子, 快速双击不会产生双任务。"""
    fam = name.split(":")[0].split("-")[0]
    jid = uuid.uuid4().hex[:8]
    with JOBS_LOCK:
        for j in JOBS.values():
            if (j["status"] == "running"
                    and j["name"].split(":")[0].split("-")[0] == fam):
                return next(k for k, v in JOBS.items() if v is j)
        JOBS[jid] = {"name": name, "status": "running", "log": [], "ts": time.time()}
    _prune_jobs()

    def worker():
        try:
            result = fn(*args, **kwargs)
            with JOBS_LOCK:
                JOBS[jid].update(status="done", result=json.loads(
                    json.dumps(result, ensure_ascii=False, default=str)))
            _notify(f"{name} 完成", _job_brief(result))
            _jobs_log(name, "done", _job_brief(result))
        except Exception as e:
            log.exception("job %s failed", name)
            with JOBS_LOCK:
                JOBS[jid].update(status="error", result=str(e)[:500])
            _notify(f"{name} 失败", str(e)[:80])
            _jobs_log(name, "error", str(e)[:120])

    threading.Thread(target=worker, daemon=True).start()
    return jid


def _notify(title: str, msg: str):
    """macOS 桌面通知(本地跑才有意义)。"""
    try:
        import subprocess as sp
        safe = msg.replace("\\", "\\\\").replace('"', '\\"')
        sp.run(["osascript", "-e",
                f'display notification "{safe}" with title "{title}"'],
               capture_output=True, timeout=5)
    except Exception:
        pass


def _jobs_log(name: str, status: str, brief: str):
    """任务结果落盘，重启后 cli.py jobs 仍能看到历史。"""
    try:
        p = DATA_DIR / "jobs.log"
        if p.exists() and p.stat().st_size > 1 << 20:   # 超1MB截断留最近500行
            p.write_text("\n".join(p.read_text(
                encoding="utf-8").splitlines()[-500:]) + "\n", encoding="utf-8")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": int(time.time()), "job": name,
                                "status": status, "brief": brief},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def _job_brief(result) -> str:
    if isinstance(result, list):
        ok = sum(1 for r in result if r.get("ok") or r.get("bvid"))
        return f"共{len(result)}条, 成功{ok}"
    return str(result)[:80]


# ---------------- API ----------------

@app.get("/")
def index():
    # no-cache: 前端频繁改，浏览器每次校验重取，避免旧JS继续跑
    return FileResponse(str(ROOT / "web" / "static" / "index.html"),
                        headers={"Cache-Control": "no-cache"})


_login_cache = {"ts": 0, "ok": None}


def _login_ok() -> bool | None:
    """探测 B站 cookie 是否有效(nav接口)，缓存5分钟。"""
    import time as _t
    from pipeline import missions
    if _login_cache["ok"] is not None and _t.time() - _login_cache["ts"] < 300:
        return _login_cache["ok"]
    if not missions._sessdata(cfg):
        _login_cache.update(ts=_t.time(), ok=False)
        return False
    try:
        r = httpx.get("https://api.bilibili.com/x/web-interface/nav",
                      headers=missions._headers(cfg), timeout=10)
        ok = bool((r.json().get("data") or {}).get("isLogin"))
    except Exception:
        ok = None
    _login_cache.update(ts=_t.time(), ok=ok)
    return ok


_disk_cache = {"ts": 0, "free": 0.0, "used": 0.0}


def _disk_stats() -> dict:
    """downloads 目录占用+剩余空间，缓存60秒。"""
    if time.time() - _disk_cache["ts"] < 60:
        return _disk_cache
    import shutil
    dl = Path(C.abs_path(cfg, C.get_setting(cfg, "download_dir")))
    used = 0.0
    try:
        used = sum(f.stat().st_size for f in dl.rglob("*") if f.is_file()) / (1 << 30)
    except OSError:
        pass
    free = shutil.disk_usage(str(dl if dl.exists() else DATA_DIR)).free / (1 << 30)
    _disk_cache.update(ts=time.time(), free=round(free, 1), used=round(used, 1))
    return _disk_cache


@app.get("/api/stats")
def stats():
    c = db.counts()
    d = _disk_stats()
    return {"counts": c, "published_today": db.published_today(),
            "disk_free_gb": d["free"], "downloads_gb": d["used"],
            "daily_limit": C.get_setting(cfg, "daily_upload_limit", 5),
            "downloaded_today": db.downloaded_today(),
            "daily_download_limit": int(C.get_setting(cfg, "daily_download_limit", 0) or 0),
            "llm_enabled": metagen.llm.is_enabled(cfg),
            "login_ok": _login_ok(),
            "extra_login": {p: multipub.logged_in(cfg, p)
                            for p in multipub.enabled_platforms(cfg)},
            "next_poll_in": (max(0, int(_sched["next_poll"] - time.time()))
                             if _sched["next_poll"] else None),
            "proxy": C.proxy(cfg) or ""}


_sched = {"next_poll": 0}


@app.get("/api/progress")
def api_progress():
    """实时处理进度: {item_id: {stage,pct,detail}}，供前端进度条轮询。"""
    from pipeline import progress
    return {"progress": progress.snapshot()}


@app.get("/api/items")
def items(status: str | None = None, line: str | None = None,
          limit: int = 100, offset: int = 0):
    rows = db.list_items(status=status, line=line, limit=limit, offset=offset)
    default_cr = int(C.get_setting(cfg, "default_copyright", 2) or 2)
    for r in rows:
        try:
            r["tags"] = json.loads(r.get("tags") or "[]")
        except Exception:
            r["tags"] = []
        if not r.get("copyright"):
            r["copyright"] = default_cr   # 空值=跟随全局默认,前端显示生效值
        # 转 /media URL
        for k in ("file_path", "cover_path"):
            p = r.get(k) or ""
            if p:
                try:
                    r[k + "_url"] = "/media/" + str(Path(p).relative_to(DATA_DIR))
                except ValueError:
                    r[k + "_url"] = ""
    return {"items": rows}


class ItemPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    tid: int | None = None
    mission_id: int | None = None
    pub_platforms: list[str] | None = None
    copyright: int | None = None
    dtime: int | None = None


@app.patch("/api/items/{item_id}")
def patch_item(item_id: int, p: ItemPatch):
    it = db.get(item_id)
    if not it:
        raise HTTPException(404, "not found")
    if it["status"] == "published":
        raise HTTPException(400, "已发布稿件不可编辑(会与线上稿件脱节)")
    if _item_busy(item_id):
        raise HTTPException(400, "该条目正在处理中，等任务结束再编辑")
    fields = {}
    if p.title is not None:
        fields["title"] = p.title[:80]
    if p.description is not None:
        fields["description"] = p.description
    if p.tags is not None:
        fields["tags"] = json.dumps([t[:20] for t in p.tags[:12]], ensure_ascii=False)
    if p.tid is not None:
        fields["tid"] = p.tid
    if p.mission_id is not None:
        fields["mission_id"] = p.mission_id
    if p.pub_platforms is not None:
        fields["pub_platforms"] = json.dumps(
            [x for x in p.pub_platforms if x in ("bilibili", "kuaishou", "tencent")],
            ensure_ascii=False)
    if p.copyright is not None and p.copyright in (1, 2):
        fields["copyright"] = p.copyright
    if p.dtime is not None:
        fields["dtime"] = p.dtime
    db.update(item_id, **fields)
    return {"ok": True}


def _item_busy(item_id: int) -> bool:
    """该条目正被下载/加工/发布中(progress 登记或专属任务在飞)。"""
    if item_id in progress.snapshot():
        return True
    with JOBS_LOCK:
        return any(j["status"] == "running"
                   and j["name"].endswith(f":{item_id}")
                   for j in JOBS.values())


@app.post("/api/items/{item_id}/skip")
def skip(item_id: int):
    """跳过即删文件腾磁盘；unskip 会清 archive 允许重新下载。"""
    item = db.get(item_id)
    if not item:
        raise HTTPException(404)
    if item["status"] == "published":
        raise HTTPException(400, "已发布稿件不可跳过")
    if _item_busy(item_id):
        raise HTTPException(400, "该条目正在处理中，等任务结束再跳过")
    db.set_status(item_id, "skipped")
    uploader._delete_files(item)
    return {"ok": True}


def _remove_archive_entry(platform: str, source_id: str):
    """从 yt-dlp archive 删除一条记录，使 unskip 后能重新下载。"""
    af = Path(C.abs_path(cfg, C.get_setting(cfg, "archive_file", "data/downloaded.archive")))
    if not af.exists() or not source_id:
        return
    try:
        key = f"{platform} {source_id}".strip()
        kept = [l for l in af.read_text().splitlines() if l.strip() != key]
        af.write_text("\n".join(kept) + "\n")
    except OSError:
        pass


@app.post("/api/items/{item_id}/unskip")
def unskip(item_id: int):
    """撤销跳过: 清 archive 记录+回 new，下次下载任务会重新抓取。"""
    item = db.get(item_id)
    if not item:
        raise HTTPException(404)
    if item["status"] != "skipped":
        raise HTTPException(400, f"status={item['status']} 不是已跳过")
    _remove_archive_entry(item.get("platform") or "", item.get("source_id") or "")
    db.unskip(item_id)
    return {"ok": True}


@app.post("/api/items/{item_id}/retry")
def retry(item_id: int):
    item = db.get(item_id)
    if not item:
        raise HTTPException(404)
    if item["status"] != "failed":
        raise HTTPException(400, f"status={item['status']} 仅失败条目可重试")
    # 失败重试：文件在 → 回 ready；文件没了(被清) → 回 new 并清 archive 允许重下
    back = ("ready" if item.get("file_path")
            and Path(item["file_path"]).exists() else "new")
    if back == "new":
        _remove_archive_entry(item.get("platform") or "", item.get("source_id") or "")
    db.set_status(item_id, back)
    return {"ok": True, "status": back}


@app.post("/api/items/{item_id}/regen")
def regen(item_id: int):
    """重新用 LLM/模板生成标题简介。"""
    item = db.get(item_id)
    if not item:
        raise HTTPException(404)
    if item["status"] == "published":
        raise HTTPException(400, "已发布稿件不可重新生成(会与线上稿件不同步)")
    if _item_busy(item_id):
        raise HTTPException(400, "该条目正在处理中，等任务结束再重新生成")
    meta = metagen.gen_meta(item, cfg, db)
    db.set_meta(item_id, meta["title"], meta["desc"], meta["tags"], meta["tid"])
    return {"ok": True, **meta}


@app.delete("/api/items/{item_id}/files")
def delete_files(item_id: int):
    """手动清理稿件本地文件(视频+封面+字幕目录)。仅已发布条目可用;
    未全平台成功的稿件请先用「补投」, 文件是重投的必要材料。"""
    item = db.get(item_id)
    if not item:
        raise HTTPException(404)
    if item["status"] != "published":
        raise HTTPException(400, "仅已发布稿件可清理文件")
    uploader._delete_files(item)
    db.update(item_id, file_path="", cover_path="", subtitle_path="")
    return {"ok": True}


@app.post("/api/items/{item_id}/publish")
def publish(item_id: int, force: bool = False, dtime: int = 0):
    if dtime:
        db.update(item_id, dtime=dtime)
    jid = run_job(f"publish:{item_id}",
                  lambda: {"bvid": uploader.publish(item_id, cfg, db, force=force)})
    return {"job_id": jid}


def _poll_and_record() -> list[dict]:
    """轮询并把每源结果落盘成源健康档案(data/source_health.json)，重启不丢。"""
    res = downloader.poll_all(cfg, db)
    try:
        health = {r["source"]: {"t": int(time.time()), "found": r["found"],
                                "new": r["new"], "skipped": r["skipped"],
                                "error": r.get("error", "")}
                  for r in res}
        (DATA_DIR / "source_health.json").write_text(
            json.dumps(health, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return res


@app.post("/api/poll")
def poll():
    jid = run_job("poll", _poll_and_record)
    return {"job_id": jid}


def _job_log(job_name: str, msg: str):
    with JOBS_LOCK:
        cur = next((j for j in JOBS.values() if j["status"] == "running"
                    and j["name"] == job_name), None)
        if cur is not None:
            cur["log"].append(msg)


@app.post("/api/download")
def download(limit: int = 0):
    """批量下载，受 daily_download_limit 每日额度限制。"""
    jid = run_job("download",
                  lambda: downloader.process_new_items(
                      cfg, db, limit=limit or None,
                      progress=lambda m: _job_log("download", m)))
    return {"job_id": jid}


@app.post("/api/items/{item_id}/download")
def download_one(item_id: int):
    """手动触发单条下载：不受每日额度限制。"""
    item = db.get(item_id)
    if not item:
        raise HTTPException(404, "not found")
    if item["status"] not in ("new", "downloaded"):
        raise HTTPException(400, f"status={item['status']} 不可下载")
    jid = run_job(f"download:{item_id}",
                  lambda: downloader.process_new_items(
                      cfg, db, item_ids=[item_id], ignore_cap=True,
                      progress=lambda m: _job_log(f"download:{item_id}", m)))
    return {"job_id": jid}


def _next_slot_ts(used: set) -> int | None:
    """下一个 ≥4小时后 的定时档位(今明两天内的 publish_slots)。
    档位容量=1条: 已占用(本次分配或库里已有dtime)的跳过。"""
    slots = C.get_setting(cfg, "publish_slots") or []
    now = time.time()
    occupied = set(used)
    for it in db.list_items(status="ready", limit=10 ** 6):
        if it.get("dtime"):
            occupied.add(int(it["dtime"]))
    for day in (0, 1):
        base = time.strftime("%Y-%m-%d", time.localtime(now + day * 86400))
        for s in sorted(slots):
            try:
                ts = time.mktime(time.strptime(f"{base} {s}", "%Y-%m-%d %H:%M"))
            except Exception:
                continue
            gap_h = C.get_setting(cfg, "publish_slot_gap_h", 4)
            if ts - now >= gap_h * 3600 and int(ts) not in occupied:
                return int(ts)
    return None


@app.post("/api/publish-all")
def publish_all(schedule: bool = False):
    """按每日限额把 ready 队列发完，自动匹配活动。
    schedule=true 时按 publish_slots 错峰定时发布。"""
    jid = run_job("publish-all", lambda: _publish_all_job(schedule))
    return {"job_id": jid}


def _publish_all_job(schedule: bool):
    limit = int(C.get_setting(cfg, "daily_upload_limit", 5))
    used_slots: set = set()
    results = []
    for item in db.list_items(status="ready", limit=10 ** 6):
        if db.published_today() >= limit:
            _job_log("publish-all", f"达到每日上限 {limit}，剩余留待明天")
            break
        if schedule and not item.get("dtime"):
            ts = _next_slot_ts(used_slots)
            if ts:
                used_slots.add(ts)
                db.update(item["id"], dtime=ts)
                _job_log("publish-all",
                         f"#{item['id']} 定时 {time.strftime('%m-%d %H:%M', time.localtime(ts))}")
        _job_log("publish-all", f"发布 #{item['id']} {(item.get('title') or '')[:40]}")
        try:
            bvid = uploader.publish(item["id"], cfg, db)
            _job_log("publish-all", f"#{item['id']} 成功 → {bvid}")
            results.append({"id": item["id"], "bvid": bvid})
        except Exception as e:
            _job_log("publish-all", f"#{item['id']} 失败: {str(e)[:120]}")
            results.append({"id": item["id"], "error": str(e)[:200]})
    return results


@app.get("/api/jobs")
def jobs():
    with JOBS_LOCK:
        return {"jobs": sorted(JOBS.values(), key=lambda j: -j["ts"])[:20]}


@app.get("/api/jobs/{jid}")
def job(jid: str):
    with JOBS_LOCK:
        j = JOBS.get(jid)
    if not j:
        raise HTTPException(404)
    return j


@app.get("/api/tids")
def tids():
    p = ROOT / "config" / "tids.json"
    return json.loads(p.read_text(encoding="utf-8"))


@app.post("/api/verify")
def verify(max_age: int = 0):
    """核对已发稿件存活状态，下架/退回标出。默认立即全量复核。"""
    return {"results": uploader.verify_published(cfg, db, max_age=max_age)}


@app.post("/api/cleanup")
def cleanup(days: int = 7):
    """清理无效条目本地文件: 孤儿目录/skipped/failed 且超 days 天未动。"""
    return downloader.cleanup_stale(cfg, db, days)


@app.get("/api/missions")
def api_missions(refresh: bool = False):
    from pipeline import missions
    return {"missions": missions.fetch_missions(cfg, force=refresh)}


@app.get("/api/sources")
def sources():
    try:
        health = json.loads((DATA_DIR / "source_health.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        health = {}
    return {"sources": [
        {**{k: s.get(k) for k in ("name", "line", "kind", "url", "tid",
                                  "category", "enabled", "limit", "subtitle")},
         "health": health.get(s["name"])}
        for s in cfg.get("sources", [])]}


def _persist_sources():
    """当前生效源列表整体写入覆盖层(增删/开关统一走这条)。"""
    C.update_overrides(lambda o: o.__setitem__("sources", cfg.get("sources", [])))


@app.post("/api/sources")
def add_source(s: dict = Body(...)):
    name = str(s.get("name") or "").strip()
    if not re.match(r"^[\w.-]{1,40}$", name):
        raise HTTPException(400, "name 仅允许字母/数字/_/./-, ≤40字符")
    if C.get_source(cfg, name):
        raise HTTPException(400, f"源 {name} 已存在")
    kind = s.get("kind") or "channel"
    if kind not in ("channel", "search"):
        raise HTTPException(400, "kind 须为 channel/search")
    line = s.get("line") or ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,19}", line):
        raise HTTPException(400, "line 须为小写字母/数字/_/- 的自定义内容线名, ≤20字符")
    url = str(s.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url 必填")
    if kind == "channel" and not re.match(r"^https?://[\w.-]+", url):
        raise HTTPException(400, "channel 源 url 须为 http(s):// 链接")
    if kind == "search" and not re.match(r"^ytsearch\d*:", url):
        raise HTTPException(400, "search 源 url 须为 ytsearch[数量]:关键词")
    if s.get("subtitle") not in (None, "", "always", "auto", "subs_only", "never"):
        raise HTTPException(400, "subtitle 须为 always/auto/subs_only/never")
    src = {"name": name, "kind": kind, "url": url,
           "line": s["line"], "enabled": True}
    for k in ("tid", "limit"):
        if s.get(k) not in (None, ""):
            try:
                src[k] = int(s[k])
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} 须为整数")
    for k in ("category", "subtitle"):
        if s.get(k) not in (None, ""):
            src[k] = s[k]
    cfg.setdefault("sources", []).append(src)
    _persist_sources()
    return {"ok": True}


@app.post("/api/sources/{name}/toggle")
def toggle_source(name: str, enabled: bool = True):
    src = C.get_source(cfg, name)
    if not src:
        raise HTTPException(404)
    src["enabled"] = bool(enabled)
    _persist_sources()
    return {"ok": True, "enabled": src["enabled"]}


@app.delete("/api/sources/{name}")
def delete_source(name: str):
    before = len(cfg.get("sources", []))
    cfg["sources"] = [s for s in cfg.get("sources", []) if s.get("name") != name]
    if len(cfg["sources"]) == before:
        raise HTTPException(404)
    _persist_sources()
    return {"ok": True}


# UI 可编辑配置白名单: {段: {键: 类型}}。覆盖层持久化+内存热更新, 不重启生效。
SETTINGS_SCHEMA = {
    "settings": {
        "proxy": str, "cookies_file": str, "max_height": int, "poll_limit": int,
        "per_source_poll_cap": int, "daily_upload_limit": int,
        "daily_download_limit": int, "default_copyright": int,
        "auto_poll_interval_min": int, "auto_workdays_only": bool,
        "auto_download_after_poll": bool, "delete_after_publish": bool,
        "publish_slots": list, "min_free_gb": int,
        "precheck_before_download": bool, "biliup_bin": str,
        "biliup_cookie": str, "download_dir": str, "archive_file": str,
        "stale_days": int, "publish_slot_gap_h": int,
        "precheck_threshold": float, "precheck_autoskip": float},
    "extra_platforms": {"*": dict},   # 任意 sau 平台: {平台id: {enabled,account}}
    "missions": {"enabled": bool, "match": dict},
    "subtitles": {
        "enabled": bool, "whisper_model": str, "whisper_device": str,
        "burn": bool, "font_size": int, "margin_v": int, "policy": dict},
    "llm": {
        "enabled": bool, "backend": str, "model": str, "timeout": int,
        "base_url": str, "api_key_env": str, "line_styles": dict},
    "blacklist": {"uploaders": list, "keywords": list},
}


def _mask_secret(sec: str, k: str, v):
    """回显脱敏: 代理里的 user:pass@ → ***@; 空/无凭据原样。"""
    if isinstance(v, str) and "://" in v and "@" in v:
        return re.sub(r"://[^/@]+@", "://***@", v)
    return v


@app.get("/api/settings")
def get_settings():
    out = {}
    for sec, keys in SETTINGS_SCHEMA.items():
        if "*" in keys:                       # 通配段: 整个 dict 原样回显
            out[sec] = cfg.get(sec) or {}
        else:
            out[sec] = {k: _mask_secret(sec, k, (cfg.get(sec) or {}).get(k))
                        for k in keys}
    return out


@app.get("/api/platforms")
def get_platforms():
    """全部可投递平台(供抽屉勾选/设置渲染): B站 + extra_platforms 全量。"""
    from pipeline import multipub
    out = {"bilibili": {"name": "B站", "enabled": True}}
    for p, c in (cfg.get("extra_platforms") or {}).items():
        out[p] = {"name": multipub.PLATFORMS.get(p, p),
                  "enabled": bool((c or {}).get("enabled"))}
    return out


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    pending = {}
    applied = {}
    for sec, fields in (body or {}).items():
        schema = SETTINGS_SCHEMA.get(sec)
        if not schema or not isinstance(fields, dict):
            continue
        for k, v in fields.items():
            typ = schema.get(k, schema.get("*"))   # "*" 通配: 任意键按该类型收
            if typ is None:
                continue
            if isinstance(v, str) and "***@" in v:
                continue            # 回显的掩码值不写回
            try:
                if typ is bool:
                    if isinstance(v, str):          # "false"→True 陷阱
                        v = v.strip().lower() in ("1", "true", "yes", "on")
                    else:
                        v = bool(v)
                elif typ is int:
                    v = int(v)
                    if v < 0:
                        raise ValueError("不能为负数")
                    if k == "default_copyright" and v not in (1, 2):
                        raise ValueError("default_copyright 须为 1 或 2")
                elif typ is float:
                    v = float(v)
                    if v < 0 or v > 1:
                        raise ValueError("须在 0~1 之间")
                elif typ is list and not isinstance(v, list):
                    v = [x for x in str(v).split(",") if x.strip()]
                elif typ is dict:
                    if not isinstance(v, dict):
                        raise ValueError("not a dict")
                    # 嵌套配置按键深合并(保留未提交的字段, 如 account/policy 其他键)
                    cur = cfg.setdefault(sec, {}).setdefault(k, {})
                    if not isinstance(cur, dict):
                        cur = cfg[sec][k] = {}
                    if sec == "extra_platforms":
                        # v 是该平台的字段字典 {enabled?, account?}
                        cur.update({kk: v[kk] for kk in ("enabled", "account")
                                    if kk in v})
                    elif sec == "subtitles" and k == "policy":
                        for pk, pv in v.items():
                            if pv in ("always", "auto", "subs_only", "never"):
                                cur[pk] = pv
                    else:
                        cur.update(v)
                    v = cur
            except (TypeError, ValueError):
                raise HTTPException(400, f"{sec}.{k} 类型错误: {v!r}")
            cfg.setdefault(sec, {})[k] = v        # 内存热更新, 新任务立即生效
            pending.setdefault(sec, {})[k] = v
            applied[f"{sec}.{k}"] = v

    def _persist(o):
        for sec, fields in pending.items():
            C._deep_merge(o.setdefault(sec, {}), fields)
    C.update_overrides(_persist)
    return {"ok": True, "applied": applied}


@app.get("/api/job-history")
def jobs_history(limit: int = 30):
    """持久化任务历史: data/jobs.log 尾部，重启/跨天可见。"""
    rows = []
    try:
        for line in (DATA_DIR / "jobs.log").read_text(
                encoding="utf-8").splitlines()[-max(1, min(limit, 200)):]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return {"history": rows[::-1]}


@app.post("/api/reconcile")
def api_reconcile():
    """手动调和: 清理 published 残留文件 / 报告 downloaded 中途态。"""
    return _reconcile()


def _scheduler():
    """常驻调度器：按配置间隔自动轮询(可选连带下载)，实现无人值守。
    每 tick 重读配置, 设置面板改动即时生效免重启。"""
    while True:
        interval = int(C.get_setting(cfg, "auto_poll_interval_min", 0) or 0)
        if interval <= 0:
            _sched["next_poll"] = 0
            time.sleep(60)
            continue
        _sched["next_poll"] = time.time() + interval * 60
        time.sleep(interval * 60)
        try:
            # 工作日限制: 周六日(5,6)整 tick 跳过,手动操作不受影响
            if (C.get_setting(cfg, "auto_workdays_only", True)
                    and time.localtime().tm_wday >= 5):
                log.info("scheduler: 周末跳过自动执行")
                continue
            # 同步轮询完再启下载，否则下载快照拿不到刚入队的条目
            _poll_and_record()
            _backup_db()
            _prune_stale_new()
            dead = uploader.verify_published(cfg, db)
            for d in dead:
                if d.get("alive") is False:
                    _notify("稿件下架", f"#{d['id']} {d['bvid']} 审核未通过/已下架")
            if bool(C.get_setting(cfg, "auto_download_after_poll", False)):
                run_job("download",
                        lambda: downloader.process_new_items(
                            cfg, db,
                            progress=lambda m: _job_log("download", m)))
        except Exception:
            log.exception("scheduler tick failed")


def _prune_stale_new(max_age_days: int = 0):
    """积压老化: new 状态超 N 天未下载 → 自动 skipped(可恢复), 保持队列新鲜。"""
    try:
        if not max_age_days:
            max_age_days = int(C.get_setting(cfg, "stale_days", 14))
        cutoff = time.time() - max_age_days * 86400
        stale = [r for r in db.list_items(status="new", limit=5000)
                 if (r.get("created_at") or 0) < cutoff]
        for r in stale:
            db.set_status(r["id"], "skipped",
                          fail_reason=f"积压>{max_age_days}天自动跳过(可在已跳过里恢复)")
        if stale:
            _notify("积压清理",
                    f"{len(stale)} 条待下载超 {max_age_days} 天已自动跳过")
            log.info("prune: %d stale new items auto-skipped", len(stale))
    except Exception:
        log.exception("prune_stale_new failed")


def _backup_db():
    """每日一份 DB 快照到 data/backups/，保留最近7份。"""
    try:
        import shutil
        src = Path(C.abs_path(cfg, C.get_setting(cfg, "db_file", "data/pipeline.db")))
        dst_dir = src.parent / "backups"
        dst_dir.mkdir(exist_ok=True)
        dst = dst_dir / f"pipeline-{time.strftime('%Y%m%d')}.db"
        if not dst.exists():
            shutil.copy2(src, dst)
        for old in sorted(dst_dir.glob("pipeline-*.db"))[:-7]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def _reconcile():
    """启动调和: 上次进程死在任务中途的残留。
    published 但本地文件还在 = 死在 multipub/删文件之间 → 告警+清文件。
    不重投多平台(重投会在视频号/快手产生重复稿)。"""
    orphans = []
    for it in db.list_items(status="published", limit=10 ** 6):
        fp = it.get("file_path") or ""
        if fp and Path(fp).exists():
            orphans.append(it["id"])
            if C.get_setting(cfg, "delete_after_publish", False):
                uploader._delete_files(it)
    if orphans:
        log.warning("reconcile: published 残留文件已清理: %s (多平台结果需人工核对 extra_pub)", orphans)
    # 进程崩溃残留的 progress 登记 → 清掉, 否则这些条目永远 _item_busy 无法操作
    with JOBS_LOCK:
        running_ids = {int(j["name"].rsplit(":", 1)[1]) for j in JOBS.values()
                       if j["status"] == "running" and ":" in j["name"]
                       and j["name"].rsplit(":", 1)[1].isdigit()}
    for iid in list(progress.snapshot()):
        if iid not in running_ids:
            progress.clear(iid)
    # 卡在 downloaded/new 但有完整文件的 = 死在加工中途, 下次下载任务会断点续跑, 仅提示
    stuck = [it["id"] for it in db.list_items(status="downloaded", limit=10 ** 6)]
    if stuck:
        log.info("reconcile: downloaded 中途态条目待续跑: %s", stuck)
    return {"orphans_cleaned": orphans, "stuck_downloaded": stuck}


def serve(host="127.0.0.1", port=8600):
    import uvicorn
    _backup_db()
    _reconcile()
    threading.Thread(target=_scheduler, daemon=True).start()
    uvicorn.run(app, host=host, port=port)
