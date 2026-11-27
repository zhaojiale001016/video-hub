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
    """桌面通知(本地跑才有意义): macOS osascript / Linux notify-send / 其他跳过。"""
    try:
        import subprocess as sp
        import sys as _s
        if _s.platform == "darwin":
            safe = msg.replace("\\", "\\\\").replace('"', '\\"')
            sp.run(["osascript", "-e",
                    f'display notification "{safe}" with title "{title}"'],
                   capture_output=True, timeout=5)
        elif _s.platform.startswith("linux"):
            sp.run(["notify-send", title, msg],
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
            "line_icons": (cfg.get("settings") or {}).get("line_icons") or {},
            "proxy": C.proxy(cfg) or ""}


@app.get("/api/platforms")
def get_platforms():
    """全部可投递平台(字典): B站 + sau 全平台 + extra_platforms 自定义 id。
    enabled 按配置; 附登录态/发稿量/创作中心链接供抽屉勾选与平台中枢共用。"""
    ep = cfg.get("extra_platforms") or {}
    counts = db.platform_pub_counts()

    def _row(pid, name, enabled, logged_in, extra=None):
        meta = multipub.PLATFORM_META.get(pid) or {}
        ok, fail = counts.get(pid, [0, 0])
        row = {"name": name, "icon": meta.get("icon", "🌐"),
               "enabled": enabled, "logged_in": logged_in,
               "ok_count": ok, "fail_count": fail,
               "links": {k: meta[k] for k in ("home", "manage", "publish")
                         if meta.get(k)}}
        row.update(extra or {})
        return row

    out = {"bilibili": _row("bilibili", "B站", True, _login_ok(),
                            {"builtin": True})}
    def _plat_cfg(c):
        """平台卡可编辑字段回显: 错峰/有头/声明/账号(封面策略另按能力给)。"""
        c = c or {}
        return {"account": c.get("account") or "main",
                "offset_min": c.get("offset_min") or 0,
                "headed": bool(c.get("headed")),
                "declaration": c.get("declaration") or ""}

    for p in multipub.SAU_PLATFORMS:
        c = ep.get(p) or {}
        extra = _plat_cfg(c)
        if p not in multipub._NO_THUMB:      # 支持封面的平台才有封面策略
            extra["cover"] = str(c.get("cover") or "file")
        out[p] = _row(p, multipub.PLATFORMS.get(p, p),
                      bool(c.get("enabled")),
                      multipub.logged_in(cfg, p), extra)
    for p, c in ep.items():                      # 自定义 id 也回显
        if p not in out and p not in multipub._RESERVED:
            out[p] = _row(p, p, bool((c or {}).get("enabled")),
                          multipub.logged_in(cfg, p),
                          {**_plat_cfg(c), "custom": True,
                           "cover": str((c or {}).get("cover") or "file")})
    return out


@app.post("/api/platforms/{pid}/open")
def platform_open(pid: str, body: dict = Body(default={})):
    """带 sau 登录态拉起真实浏览器直达创作平台页(target=home/manage/publish)。"""
    if pid not in get_platforms():          # 未知 pid 拒在 glob 之前
        raise HTTPException(404, f"未知平台 {pid}")
    ok, msg = multipub.open_creator(cfg, pid, (body or {}).get("target", "home"))
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "url": msg}


@app.post("/api/platforms/{pid}/login")
def platform_login(pid: str):
    """拉起 sau 扫码登录浏览器(B站走 /api/bili/qrcode 的 QR 通道)。"""
    if pid == "bilibili":
        raise HTTPException(400, "B站用扫码登录(B站APP)")
    if pid not in get_platforms():
        raise HTTPException(404, f"未知平台 {pid}")
    cmd, cwd = multipub.login_cmd(pid, cfg)
    import subprocess
    subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return {"ok": True}


@app.get("/api/bili/qrcode")
def bili_qrcode():
    """B站扫码登录: 生成 TV 二维码(B站APP扫)。不依赖 biliup 二进制/终端,
    Windows/服务器等无终端环境也能登录。"""
    import base64
    import io

    import qrcode
    st = uploader.qr_login_start(cfg)
    buf = io.BytesIO()
    qrcode.make(st["url"]).save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"auth_code": st["auth_code"],
            "img": f"data:image/png;base64,{b64}"}


@app.get("/api/bili/qrcode/poll")
def bili_qrcode_poll(auth_code: str = ""):
    r = uploader.qr_login_poll(cfg, auth_code)
    if r.get("status") == "done":
        _login_cache["ok"] = None            # 登录态缓存立即失效重查
    return r


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
        # 转可预览 URL: data/ 内走 /media 静态目录; download_dir 指到 data/ 外时
        # relative_to 失败——回退到条目作用域的文件端点, 任意位置都能预览
        for k, kind in (("file_path", "video"), ("cover_path", "cover")):
            p = r.get(k) or ""
            url = ""
            if p:
                try:
                    url = "/media/" + Path(p).relative_to(DATA_DIR).as_posix()
                except ValueError:
                    url = f"/api/items/{r['id']}/file?kind={kind}"
            r[k + "_url"] = url
    return {"items": rows}


@app.get("/api/items/{item_id}/file")
def item_file(item_id: int, kind: str = "video"):
    """条目文件流(file_path/cover_path)——download_dir 不在 data/ 下时也能预览。"""
    it = db.get(item_id)
    if not it:
        raise HTTPException(404, "条目不存在")
    p = it.get("cover_path" if kind == "cover" else "file_path") or ""
    fp = Path(p)
    if not p or not fp.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(fp))


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
        # 平台 id: bilibili + 已配置/已知的 sau 平台; 未知 id 按 slug 规则放行
        # (投递时按 enabled 过滤, 存着不碍事——保留用户意图)
        import re as _re
        fields["pub_platforms"] = json.dumps(
            [x for x in p.pub_platforms
             if isinstance(x, str) and _re.fullmatch(r"[a-z][a-z0-9_-]{0,19}", x)],
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


@app.post("/api/import")
def import_local(body: dict = Body(...)):
    """导入本地视频文件进流水线(复制进 download_dir/<id>/, 默认自制版权)。
    process=true 时异步接字幕+元数据→ready, 返回 job_id; 否则停在 new。"""
    path = str(body.get("path") or "").strip()
    if not path:
        raise HTTPException(400, "path 必填(本机绝对路径)")
    line = str(body.get("line") or "local").strip()
    try:
        r = downloader.import_local_file(path, cfg, db, line=line,
                                         title=str(body.get("title") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"导入失败: {e}")
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "导入失败")
    if not body.get("process", True):
        return r
    jid = run_job(f"import:{r['id']}",
                  lambda: downloader.process_new_items(
                      cfg, db, item_ids=[r["id"]], ignore_cap=True,
                      progress=lambda m: _job_log(f"import:{r['id']}", m)))
    return {**r, "job_id": jid}


def _next_slot_ts(used: set) -> int | None:
    """下一个 ≥4小时后 的定时档位(今明两天内的 publish_slots)。
    档位容量=1条: 已占用(本次分配或库里已有dtime)的跳过。"""
    slots = C.get_setting(cfg, "publish_slots") or []
    now = time.time()
    occupied = set(used)
    for st in ("ready", "failed"):   # failed 重试会带原 dtime 回 ready, 也算占用
        for it in db.list_items(status=st, limit=10 ** 6):
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


@app.post("/api/verify-code")
def verify_code(payload: dict):
    """抖音等平台投递触发短信验证时, 把收到的验证码喂给等待中的 sau 进程。
    sau douyin 轮询 vendor/sau/verify_code.txt, 写入后下一轮自动拾取提交。"""
    code = str((payload or {}).get("code") or "").strip()
    if not code.isdigit() or not (4 <= len(code) <= 8):
        return {"ok": False, "error": "验证码应为 4-8 位数字"}
    from pipeline import multipub
    f = Path(C.abs_path(cfg, multipub.SAU_DIR)) / "verify_code.txt"
    f.write_text(code, encoding="utf-8")
    return {"ok": True, "path": str(f)}


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
        "precheck_threshold": float, "precheck_autoskip": float,
        "submit_api": str, "tencent_content_label": str,
        "line_icons": dict},                 # {线slug: ["emoji","显示名"]}, 前端图标映射
    "extra_platforms": {"*": dict},   # 任意 sau 平台: {平台id: {enabled,account,cover,offset_min,headed,declaration}}
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
            if sec == "extra_platforms":
                # 平台 id 会拼进 cookie 文件名/subprocess 参数 → 强制 slug+拒保留字
                if err := multipub.valid_platform_id(k):
                    raise HTTPException(400, err)
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
                        # v 是该平台的字段字典 {enabled?, account?, cover?,
                        # offset_min?, headed?, declaration?};
                        # account 拼进 cookie 文件名 → 校验 slug
                        acct = v.get("account")
                        if acct is not None and not re.fullmatch(
                                r"[a-zA-Z0-9_-]{1,30}", str(acct)):
                            raise HTTPException(
                                400, f"extra_platforms.{k}.account 非法: {acct!r}")
                        if v.get("cover") is not None and \
                                v["cover"] not in ("file", "auto"):
                            raise HTTPException(
                                400, f"extra_platforms.{k}.cover 非法: {v['cover']!r}")
                        if v.get("offset_min") is not None:
                            try:
                                v["offset_min"] = max(0, int(v["offset_min"]))
                            except (TypeError, ValueError):
                                raise HTTPException(
                                    400, f"extra_platforms.{k}.offset_min 非法")
                        cur.update({kk: v[kk] for kk in (
                            "enabled", "account", "cover", "offset_min",
                            "headed", "declaration") if kk in v})
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
    每 tick 重读配置, 设置面板改动即时生效免重启。
    启动后 2 分钟先补一轮(否则重启把首轮推迟一整个间隔)。"""
    first = True
    while True:
        interval = int(C.get_setting(cfg, "auto_poll_interval_min", 0) or 0)
        if interval <= 0:
            _sched["next_poll"] = 0
            time.sleep(60)
            continue
        wait = 120 if first else interval * 60
        deadline = time.time() + wait
        _sched["next_poll"] = deadline
        first = False
        # 墙钟到点制: 分小段睡并比对时间。系统睡眠会冻结 monotonic sleep,
        # 唤醒后 deadline 已过 → 立即补 tick, 不再把当轮推迟整个间隔
        while time.time() < deadline:
            time.sleep(min(60, max(1, deadline - time.time())))
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
        max_age_days = max_age_days or int(C.get_setting(cfg, "stale_days", 14))
        if max_age_days <= 0:
            return                            # 0 = 关闭积压清理
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
    published 且全平台已投完但文件还在 = 死在删文件前 → 补删;
    部分成功的稿件文件是补投材料, 必须保留。"""
    orphans, pending = [], []
    auto_del = C.get_setting(cfg, "delete_after_publish", False)
    for it in db.list_items(status="published", limit=10 ** 6):
        fp = it.get("file_path") or ""
        if not (fp and Path(fp).exists()):
            continue
        if uploader.all_selected_done(it, cfg):
            orphans.append(it["id"])
            if auto_del:
                uploader._delete_files(it)
        else:
            pending.append(it["id"])
    if orphans:
        log.warning("reconcile: 已发布残留文件%s: %s",
                    "已清理" if auto_del else "待清理(开启 delete_after_publish 自动清)", orphans)
    if pending:
        log.info("reconcile: 部分平台未投完, 文件保留待补投: %s", pending)
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
    return {"orphans": orphans, "pending_retry": pending,
            "stuck_downloaded": stuck}


def serve(host="127.0.0.1", port=8600):
    """管理台 API 无鉴权, 只允许监听本机回环。
    非本机绑定一律拒绝启动——如需局域网访问请自行加反代鉴权
    (显式放开: VIDEOHUB_ALLOW_REMOTE=1, 后果自负)。"""
    import os
    import uvicorn
    loopback = {"127.0.0.1", "localhost", "::1"}
    if host not in loopback and os.environ.get("VIDEOHUB_ALLOW_REMOTE") != "1":
        raise SystemExit(
            f"拒绝监听 {host}: API 无鉴权, 仅允许 {sorted(loopback)}。"
            "确需远程请自加鉴权反代, 或 VIDEOHUB_ALLOW_REMOTE=1 自担风险")
    _backup_db()
    _reconcile()
    threading.Thread(target=_scheduler, daemon=True).start()
    uvicorn.run(app, host=host, port=port)
