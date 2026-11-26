"""biliup-rs 投稿封装：转载投稿(copyright=2 + source链接)。"""
import json
import logging
import re
import subprocess
from pathlib import Path

from . import config as C
from .db import DB

log = logging.getLogger("uploader")


class PublishError(Exception):
    pass


def _bin(cfg: dict) -> str:
    return C.abs_path(cfg, C.get_setting(cfg, "biliup_bin", "bin/biliup"))


def _cookie(cfg: dict) -> str:
    return C.abs_path(cfg, C.get_setting(cfg, "biliup_cookie", "data/cookies.json"))


def _delete_files(item: dict):
    """删除条目本地文件(视频+封面+字幕整目录)，DB记录保留用于去重。"""
    try:
        import shutil
        fp = item.get("file_path")
        if fp:
            shutil.rmtree(Path(fp).parent, ignore_errors=True)
    except OSError:
        pass


def _to_bytes(v: str, unit: str) -> float:
    return float(v) * {"B": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30,
                       "TiB": 2**40, "KB": 1e3, "MB": 1e6, "GB": 1e9}.get(unit, 1)


def check_daily_limit(db: DB, cfg: dict):
    limit = int(C.get_setting(cfg, "daily_upload_limit", 5))
    done = db.published_today()
    if done >= limit:
        raise PublishError(f"已达每日投稿上限 {done}/{limit}")


def effective_copyright(item: dict, cfg: dict) -> int:
    """条目值 > 全局默认 > 2。1=自制(可参加活动) 2=转载(需source,禁活动)。"""
    return int(item.get("copyright")
               or C.get_setting(cfg, "default_copyright", 2) or 2)


def build_upload_cmd(item: dict, cfg: dict) -> list[str]:
    tags = item.get("tags") or "[]"
    if isinstance(tags, str):
        tags = json.loads(tags)
    cr = effective_copyright(item, cfg)
    cmd = [_bin(cfg), "-u", _cookie(cfg), "upload", item["file_path"],
           "--copyright", str(cr),
           "--tid", str(item.get("tid") or 21),
           "--title", (item.get("title") or "")[:80],
           "--desc", (item.get("description") or "")[:1500],
           "--tag", ",".join(tags[:12]),
           "--submit", "web"]
    if cr == 2:
        cmd += ["--source", item.get("source_url") or ""]
    if item.get("cover_path"):
        cmd += ["--cover", item["cover_path"]]
    # 转载稿(copyright=2)不支持活动(B站 code 21071); 自制稿才传 mission-id
    if cr == 1 and item.get("mission_id"):
        cmd += ["--mission-id", str(item["mission_id"])]
    dtime = item.get("dtime")
    if dtime:
        cmd += ["--dtime", str(dtime)]
    return cmd


def selected_platforms(item: dict) -> set | None:
    """发布目标集合: pub_platforms="" → None(全部, 含B站);
    列表 → 勾选的子集, 可不含 bilibili(只发多平台)。
    空列表 → 用户一个平台都没勾。"""
    sel = (item.get("pub_platforms") or "").strip()
    if not sel:
        return None
    try:
        return set(json.loads(sel))
    except Exception:
        return None


def required_platforms(item: dict, cfg: dict) -> set:
    """实际需要投递成功的平台: 勾选 ∩ 可投递(B站 + 已启用的多平台)。
    勾选但未启用的平台不计入(无法投递, 不应阻塞清理)。"""
    from . import multipub
    sel = selected_platforms(item)
    deliverable = {"bilibili"} | set(multipub.enabled_platforms(cfg))
    return deliverable if sel is None else (set(sel) & deliverable)


def all_selected_done(item: dict, cfg: dict, res: dict | None = None) -> bool:
    """所有勾选平台是否都已成功(extra_pub ok:true / 有 bvid)。
    res 传入时优先于条目里的 extra_pub(本轮回写前判断用)。"""
    if res is None:
        try:
            res = json.loads(item.get("extra_pub") or "{}")
        except Exception:
            res = {}
    done = {p for p, r in res.items() if r.get("ok") is True}
    if item.get("bvid"):
        done.add("bilibili")
    req = required_platforms(item, cfg)
    return bool(req) and req <= done


def _extra_results(item: dict, extra: list[dict]) -> str:
    return json.dumps(
        {r["platform"]: {"ok": r["ok"], "name": r["name"],
                         **({"msg": r["msg"][-200:]} if not r["ok"] else {})}
         for r in extra}, ensure_ascii=False)


def publish(item_id: int, cfg: dict, db: DB, dry_run: bool = False,
            force: bool = False) -> str:
    """投稿一个 ready 条目，成功返回 bvid(未投B站则返回"")。force=True 跳过撞稿预检。
    pub_platforms 圈定发布目标: 不含 bilibili → 只投多平台, 与B站流程完全解耦。"""
    item = db.get(item_id)
    if not item:
        raise PublishError(f"item {item_id} not found")
    if item.get("status") not in ("ready", "failed", "published"):
        raise PublishError(f"status={item.get('status')} 不可投稿(需 ready)")
    if not item.get("file_path"):
        raise PublishError("缺少视频文件")
    sel = selected_platforms(item)
    if sel is not None and not sel:
        raise PublishError("未勾选任何发布平台")
    # 重试去重: extra_pub 里已成功的平台不再重投(防视频号/快手重复稿件)
    try:
        prev = json.loads(item.get("extra_pub") or "{}")
    except Exception:
        prev = {}
    done = {p for p, r in prev.items() if r.get("ok") is True}
    if item.get("bvid"):
        done.add("bilibili")
    from . import multipub
    enabled_multi = multipub.enabled_platforms(cfg)
    want_bili = (sel is None or "bilibili" in sel) and "bilibili" not in done
    base = enabled_multi if sel is None else [p for p in sel if p != "bilibili"]
    multi_wanted = {p for p in base if p in enabled_multi and p not in done}

    if not want_bili and not multi_wanted:
        # 勾选平台全部已成功过 → 只补状态不重投
        db.mark_published(item_id, item.get("bvid") or "", "全部平台已投递(去重)")
        if C.get_setting(cfg, "delete_after_publish", False):
            _delete_files(item)
        return item.get("bvid") or ""

    if not want_bili:
        extra = multipub.publish_all(item, cfg, allow=multi_wanted)
        if not extra:
            raise PublishError("未启用任何已勾选的发布平台")
        merged = dict(prev)
        merged.setdefault("bilibili", {"ok": None, "name": "B站", "msg": "未勾选"})
        merged.update(json.loads(_extra_results(item, extra)))
        db.update(item_id, extra_pub=json.dumps(merged, ensure_ascii=False))
        for r in extra:
            log.info("item %s → %s: %s", item_id, r["name"],
                     "OK" if r["ok"] else r["msg"][-120:])
        from . import progress as _prog
        _prog.clear(item_id)
        if any(r["ok"] for r in extra):
            db.mark_published(item_id, item.get("bvid") or "", "仅多平台投递")
            if C.get_setting(cfg, "delete_after_publish", False) \
                    and all_selected_done(item, cfg, merged):
                _delete_files(item)
            return item.get("bvid") or ""
        db.set_status(item_id, "failed", fail_reason="多平台投递全部失败")
        raise PublishError("多平台投递全部失败")
    cr = effective_copyright(item, cfg)
    results = dict(prev)
    # B站域前置校验: 任何阻断只记B站失败, 不拦截其他勾选平台
    bili_block = None
    if cr == 2 and not item.get("source_url"):
        bili_block = "转载投稿必须有 source_url"
    elif item.get("title"):
        from difflib import SequenceMatcher
        for dup in db.list_items(status="published", limit=1000):
            t = dup.get("title") or ""
            if t and (t == item["title"]
                      or SequenceMatcher(None, t, item["title"]).ratio() >= 0.9):
                bili_block = (f"标题与已发布 #{dup['id']}({dup.get('bvid','')}) "
                              "高度相似，请改标题")
                break
    if not bili_block:
        try:
            check_daily_limit(db, cfg)
        except PublishError as e:
            bili_block = str(e)
    if not bili_block and not force:  # 站内撞稿预检
        from . import precheck
        hit = precheck.check_collision(cfg, item, db=db)
        if hit:
            msg = (f"撞稿(相似度{hit['sim']:.0%}): {hit['bvid']} "
                   f"《{hit['title']}》@{hit['author']}")
            if hit["sim"] >= 0.85:
                # 高度相似=确认重复: 该内容已在B站存在, 全平台停发+清理
                db.set_status(item_id, "skipped",
                              fail_reason=f"{msg} —— 已自动清理")
                _delete_files(item)
                raise PublishError(f"{msg} —— 已自动跳过+删除文件")
            bili_block = msg + " —— 确认后点「强制发」"

    from . import progress
    bvid, out = "", ""
    if bili_block:
        if dry_run:
            return "DRY: B站域阻断: " + bili_block
        results["bilibili"] = {"ok": False, "name": "B站", "msg": bili_block[:200]}
        log.warning("item %s B站域阻断(其余平台照常): %s", item_id, bili_block)
    else:
        # 自制稿(1)才允许参加活动: 转载稿B站 code 21071 拒收
        if cr == 1 and not item.get("mission_id"):
            from . import missions
            m = missions.pick_mission(cfg, item.get("line", ""), item)
            if m:
                tags = json.loads(item.get("tags") or "[]")
                if m.get("tag") and m["tag"] not in tags and len(tags) < 12:
                    tags.append(m["tag"])
                db.update(item_id, mission_id=m["mission_id"],
                          tags=json.dumps(tags, ensure_ascii=False))
                item = db.get(item_id)
                log.info("item %s → mission %s(%s)", item_id, m["name"], m["mission_id"])

        cmd = build_upload_cmd(item, cfg)
        if dry_run:
            return "DRY: " + " ".join(cmd)

        # Popen 流式读输出: biliup 进度条(\r刷新)逐段解析成上传百分比
        import time as _t
        progress.set_stage(item_id, "上传中", 0, "启动 biliup")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="ignore")
        parts, last, t0 = [], "", _t.time()
        try:
            while True:
                if _t.time() - t0 > 3600:
                    proc.kill()
                    break
                ch = proc.stdout.read(1)
                if not ch:
                    break
                last += ch
                if ch in "\r\n":
                    parts.append(last)
                    frag = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", last).strip()
                    last = ""
                    m = re.search(r"([\d.]+)\s*([KMGT]i?B)\s*/\s*([\d.]+)\s*([KMGT]i?B)", frag)
                    mp = re.search(r"([\d.]+)\s*%", frag)
                    if m:
                        pct = _to_bytes(m.group(1), m.group(2)) / max(
                            _to_bytes(m.group(3), m.group(4)), 1)
                        progress.set_stage(
                            item_id, "上传中", pct,
                            f"共{_to_bytes(m.group(3), m.group(4))/1e6:.0f}MB")
                    elif mp:
                        progress.set_stage(item_id, "上传中",
                                           float(mp.group(1)) / 100, frag[-60:])
                    elif len(frag) > 4 and "{" not in frag:
                        progress.set_stage(item_id, "上传中", None, frag[-60:])
            proc.wait()
            out = ("".join(parts) + last)
        finally:
            progress.clear(item_id)
        log.info("biliup out: %s", out[-2000:])

        m = re.search(r"BV1[A-Za-z0-9]{9}", out)
        if proc.returncode == 0 and m:
            bvid = m.group(0)
            results["bilibili"] = {"ok": True, "name": "B站", "bvid": bvid}
        else:
            results["bilibili"] = {"ok": False, "name": "B站",
                                   "msg": out[-200:].replace("\r", " ")}

    # 多平台投递: 无论B站成败, 剩余勾选平台照常执行
    extra = []
    if multi_wanted:
        try:
            extra = multipub.publish_all(item, cfg, allow=multi_wanted)
            if extra:
                results.update(json.loads(_extra_results(item, extra)))
                for r in extra:
                    log.info("item %s → %s: %s", item_id, r["name"],
                             "OK" if r["ok"] else r["msg"][-120:])
        except Exception as e:
            log.warning("multipub error: %s", e)
    db.update(item_id, extra_pub=json.dumps(results, ensure_ascii=False))
    progress.clear(item_id)   # multipub 各阶段进度兜底清理

    if bvid or any(r["ok"] for r in extra):
        db.mark_published(item_id, bvid or item.get("bvid") or "",
                          out[-1000:] if bvid else "多平台投递成功(B站未成功)")
        if C.get_setting(cfg, "delete_after_publish", False) \
                and all_selected_done(item, cfg, results):
            _delete_files(item)
        return bvid or item.get("bvid") or ""
    if done:  # 历史已有平台成功, 保持 published 不回退
        raise PublishError("本轮重试平台全部失败(已有成功平台, 保持 published)")
    db.set_status(item_id, "failed", fail_reason="全部勾选平台投递失败")
    raise PublishError(
        f"投稿失败: B站={results.get('bilibili',{}).get('msg','')[:200]} "
        + "; ".join(f"{r['name']}={r['msg'][-100:]}" for r in extra if not r["ok"]))


def verify_published(cfg: dict, db: DB, max_age: int = 86400) -> list[dict]:
    """核对已发稿件在B站的存活状态。下架/退回的标 fail_reason。
    每条每 max_age 秒复核一次。"""
    import httpx
    from . import missions
    headers = missions._headers(cfg)  # 带 buvid3 防 412 风控
    out = []
    for it in db.published_unverified(max_age):
        bvid = it.get("bvid")
        if not bvid:   # 仅多平台投递的条目没有bvid, 无需B站复核
            db.mark_verified(it["id"])
            continue
        try:
            r = httpx.get("https://api.bilibili.com/x/web-interface/view",
                          params={"bvid": bvid}, headers=headers, timeout=15)
            code = r.json().get("code")
        except Exception as e:
            log.warning("verify %s failed: %s", bvid, e)
            continue
        if code == 0:
            db.mark_verified(it["id"])
            out.append({"id": it["id"], "bvid": bvid, "alive": True})
        elif code in (-404, 62002, 62004):
            db.mark_verified(it["id"],
                             fail_reason="稿件已下架/审核未通过，本地文件可能已清理")
            out.append({"id": it["id"], "bvid": bvid, "alive": False, "code": code})
        else:
            db.mark_verified(it["id"])
            out.append({"id": it["id"], "bvid": bvid, "alive": None, "code": code})
    return out


def login(cfg: dict):
    """交互式扫码登录（在终端跑），写 cookies.json。"""
    subprocess.call([_bin(cfg), "-u", _cookie(cfg), "login"])
