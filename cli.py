#!/usr/bin/env python3
"""搬运流水线 CLI：poll / download / publish / login / serve / stats"""
import argparse
import json
import logging
import subprocess
import sys

from pipeline import config as C
from pipeline import downloader, uploader
from pipeline.db import DB

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

ROOT = __file__ and __import__("pathlib").Path(__file__).resolve().parent


def _release_plugin(version: str, commit: str = "") -> dict:
    """DSH 插件发版: ① package.json version bump ② dsh-index.json 前置新版本条目
    (commit 默认当前 HEAD——先提交插件改动再跑本命令)。返回改动摘要。"""
    import re
    from pathlib import Path
    if not re.fullmatch(r"\d+\.\d+\.\d+(-[\w.]+)?", version):
        raise SystemExit(f"版本号非法: {version}")
    pkg_p = ROOT / "plugins" / "dsh-video-hub" / "package.json"
    idx_p = ROOT / "docs" / "dsh-index.json"
    pkg = json.loads(pkg_p.read_text(encoding="utf-8"))
    old = pkg.get("version")
    pkg["version"] = version
    pkg_p.write_text(json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ref = commit or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    idx = json.loads(idx_p.read_text(encoding="utf-8"))
    plug = idx["plugins"][0]
    entry = {
        "version": version,
        "publishTimestamp": int(__import__("time").time()),
        "source": {"type": "git",
                   "url": "https://github.com/zhaojiale001016/video-hub",
                   "commit": ref,
                   "path": "plugins/dsh-video-hub"},
        "engines": pkg.get("engines"),
    }
    plug["versions"] = [e for e in plug.get("versions", [])
                        if e.get("version") != version]
    plug["versions"].insert(0, entry)
    idx_p.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"plugin": f"{old} → {version}", "index_commit": ref[:8],
            "next": "git add -A && git commit && git tag vX.Y.Z && git push --tags"}


def main():
    ap = argparse.ArgumentParser(description="Video Hub · 视频搬运中台")
    ap.add_argument("-c", "--config", help="sources.yaml 路径")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("poll", help="轮询所有启用的订阅源")
    p = sub.add_parser("download", help="下载待处理条目并生成元数据(受每日额度限制)")
    p.add_argument("--limit", type=int, default=0, help="0=处理整队")
    p.add_argument("--id", type=int, default=0, help="手动下载单条，不受每日额度限制")
    p = sub.add_parser("genmeta", help="重新生成单条元数据")
    p.add_argument("id", type=int)
    p = sub.add_parser("publish", help="投稿单条(需ready)")
    p.add_argument("id", type=int)
    p.add_argument("--dry", action="store_true", help="只打印投稿命令不执行")
    p.add_argument("--force", action="store_true", help="跳过撞稿预检")
    p.add_argument("--dtime", type=int, default=0, help="定时发布10位时间戳(≥4h后)")
    sub.add_parser("publish-all", help="按每日限额把ready队列发完")
    sub.add_parser("doctor", help="体检: 登录态/活动/队列/磁盘")
    sub.add_parser("login", help="B站扫码登录(写 cookies.json)")
    sub.add_parser("login-ks", help="快手扫码登录(弹浏览器,手机快手APP扫码)")
    sub.add_parser("login-sph", help="视频号扫码登录(弹浏览器,微信扫码)")
    p = sub.add_parser("login-extra", help="任意 sau 平台扫码登录(如 douyin/xiaohongshu)")
    p.add_argument("platform", help="平台id(见 vendor/sau/sau_cli.py PLATFORMS)")
    sub.add_parser("renew", help="刷新登录态")
    sub.add_parser("missions", help="列出当前可参加的B站投稿活动(需登录)")
    p = sub.add_parser("verify", help="核对已发稿件存活状态(下架/退回标出)")
    p.add_argument("--all", action="store_true", help="立即复核全部(无视24h缓存)")
    p = sub.add_parser("cleanup", help="清理 skipped/failed/孤儿目录的本地文件")
    sub.add_parser("reconcile", help="调和: 上次进程死在任务中途的残留(published残留文件/中途态条目)")
    p.add_argument("--days", type=int, default=7, help="清理超过N天未动的文件")
    sub.add_parser("jobs", help="查看最近后台任务历史(含服务重启前的)")
    p = sub.add_parser("unskip", help="撤销跳过(清archive记录,回new重新下载)")
    p.add_argument("id", type=int)
    p = sub.add_parser("import", help="导入本地视频文件进流水线(复制入队+字幕+元数据)")
    p.add_argument("path", help="视频文件绝对路径(mp4/mkv/webm/mov/flv/avi/ts/m4v)")
    p.add_argument("--line", default="local", help="内容线 slug(默认 local)")
    p.add_argument("--title", default="", help="原标题(默认取文件名)")
    p.add_argument("--no-process", action="store_true", help="只入库不处理(停 new)")
    p = sub.add_parser("release-plugin", help="DSH插件发版: 同步 package.json 版本+dsh-index.json 条目")
    p.add_argument("version", help="插件新版本号, 如 0.2.0")
    p.add_argument("--commit", default="", help="index 指向的 git ref(默认当前 HEAD)")
    p = sub.add_parser("list", help="列出条目")
    p.add_argument("--status", default="ready")
    sub.add_parser("stats", help="统计")
    p = sub.add_parser("serve", help="启动Web管理台")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8600)
    sub.add_parser("mcp", help="启动 MCP stdio server(接 Claude/Cursor/Devin 等 AI 工具)")

    args = ap.parse_args()
    cfg = C.load_config(args.config)
    db = DB(C.abs_path(cfg, C.get_setting(cfg, "db_file", "data/pipeline.db")))

    if args.cmd == "import":
        try:
            r = downloader.import_local_file(args.path, cfg, db, line=args.line,
                                             title=args.title)
        except ValueError as e:
            sys.exit(f"导入失败: {e}")
        if not r.get("ok"):
            sys.exit(f"导入失败: {r.get('error')}")
        print(f"导入 #{r['id']} → {r['file_path']}")
        if not args.no_process:
            downloader.process_new_items(
                cfg, db, item_ids=[r["id"]], ignore_cap=True,
                progress=lambda m: print(m, flush=True))
    elif args.cmd == "release-plugin":
        r = _release_plugin(args.version, args.commit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.cmd == "poll":
        for st in downloader.poll_all(cfg, db):
            print(json.dumps(st, ensure_ascii=False))
    elif args.cmd == "download":
        downloader.process_new_items(
            cfg, db, limit=args.limit or None,
            item_ids=[args.id] if args.id else None, ignore_cap=bool(args.id),
            progress=lambda m: print(m, flush=True))
    elif args.cmd == "genmeta":
        from pipeline import metagen
        item = db.get(args.id)
        meta = metagen.gen_meta(item, cfg, db)
        db.set_meta(args.id, meta["title"], meta["desc"], meta["tags"], meta["tid"])
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    elif args.cmd == "publish":
        if args.dtime:
            db.update(args.id, dtime=args.dtime)
        print(uploader.publish(args.id, cfg, db, dry_run=args.dry, force=args.force))
    elif args.cmd == "doctor":
        from pipeline import missions
        ok = missions._sessdata(cfg)
        print(f"cookies.json: {'存在' if ok else '缺失(跑 cli.py login)'}")
        if ok:
            import httpx
            try:
                r = httpx.get("https://api.bilibili.com/x/web-interface/nav",
                              headers=missions._headers(cfg), timeout=10)
                d = r.json().get("data") or {}
                print(f"登录态: {'有效' if d.get('isLogin') else '已过期(跑 cli.py renew/login)'}"
                      f"  uid={d.get('mid','?')}")
            except Exception as e:
                print(f"登录态检查失败: {e}")
        print(f"活动: {len(missions.fetch_missions(cfg, force=True))} 个可参加")
        from pipeline import multipub
        for p in multipub.enabled_platforms(cfg):
            st = "已登录" if multipub.logged_in(cfg, p) else f"未登录(跑 cli.py login-extra {p})"
            print(f"{multipub.PLATFORMS.get(p, p)}: {st}")
        print(f"队列: {db.counts()}  今日已投 {db.published_today()}")
        # 二进制可解析性(Windows 常见坑)
        import shutil as _sh
        bp = uploader._bin(cfg)
        print(f"biliup: {bp} {'✓' if Path(bp).exists() else '✗ 未找到(bin/biliup[.exe] 或 biliup_bin 设置)'}")
        for tool in ("ffmpeg", "ffprobe"):
            print(f"{tool}: {_sh.which(tool) or '✗ 未找到(装 ffmpeg 并加入 PATH)'}")
        sau_py = multipub._sau_py(cfg)
        if (cfg.get('extra_platforms') or {}):
            print(f"sau python: {sau_py} {'✓' if Path(sau_py).exists() else '✗ 需先在 vendor/sau 建 venv(见 README 步骤4)'}")
    elif args.cmd == "publish-all":
        limit = int(C.get_setting(cfg, "daily_upload_limit", 5))
        for it in db.list_items(status="ready", limit=10 ** 6):
            if db.published_today() >= limit:
                print(f"达到每日上限 {limit}")
                break
            try:
                print(f"#{it['id']} → {uploader.publish(it['id'], cfg, db)}")
            except Exception as e:
                print(f"#{it['id']} 失败: {e}")
    elif args.cmd == "login":
        uploader.login(cfg)
    elif args.cmd in ("login-ks", "login-sph", "login-extra"):
        from pipeline import multipub
        platform = {"login-ks": "kuaishou", "login-sph": "tencent"}.get(
            args.cmd, args.platform if args.cmd == "login-extra" else None)
        if err := multipub.valid_platform_id(platform):
            sys.exit(f"平台id非法: {err}")
        cmd, cwd = multipub.login_cmd(platform, cfg)
        print(f"即将弹出浏览器，请登录{multipub.PLATFORMS.get(platform, platform)}…")
        subprocess.call(cmd, cwd=cwd)
    elif args.cmd == "renew":
        subprocess.call([uploader._bin(cfg), "-u", uploader._cookie(cfg), "renew"])
    elif args.cmd == "missions":
        from pipeline import missions
        ms = missions.fetch_missions(cfg, force=True)
        if not ms:
            print("未取到活动列表，先 `cli.py login` 登录")
        for m in ms:
            print(f'{m["mission_id"]:>10}  {m["name"]}  tag={m.get("tag","")}')
    elif args.cmd == "verify":
        for r in uploader.verify_published(cfg, db, max_age=0 if args.all else 86400):
            st = "存活" if r["alive"] else ("已下架/退回" if r["alive"] is False else f"未知({r.get('code')})")
            print(f"#{r['id']} {r['bvid']} → {st}")
    elif args.cmd == "cleanup":
        r = downloader.cleanup_stale(cfg, db, args.days)
        print(f"清理完成: 释放 {r['freed_gb']}GB, 删除目录 {r['removed']}")
    elif args.cmd == "reconcile":
        from web.app import _reconcile
        print(json.dumps(_reconcile(), ensure_ascii=False))
    elif args.cmd == "jobs":
        p = C.abs_path(cfg, "data/jobs.log")
        try:
            lines = open(p, encoding="utf-8").read().strip().splitlines()[-15:]
            import time as _t
            for l in lines:
                j = json.loads(l)
                print(_t.strftime("%m-%d %H:%M", _t.localtime(j["t"])),
                      f"[{j['status']}]", j["job"], "-", j["brief"])
        except FileNotFoundError:
            print("暂无任务历史")
    elif args.cmd == "unskip":
        it = db.get(args.id)
        if not it or it["status"] != "skipped":
            sys.exit(f"#{args.id} 不是已跳过状态")
        af = C.abs_path(cfg, C.get_setting(cfg, "archive_file", "data/downloaded.archive"))
        try:
            key = f"{it['platform']} {it['source_id']}".strip()
            kept = [l for l in open(af, encoding="utf-8").read().splitlines()
                    if l.strip() != key]
            open(af, "w", encoding="utf-8").write("\n".join(kept) + "\n")
        except FileNotFoundError:
            pass
        db.unskip(args.id)
        print(f"#{args.id} 已恢复 new，清掉了 archive 记录")
    elif args.cmd == "list":
        for it in db.list_items(status=args.status):
            print(f'#{it["id"]} [{it["line"]}] {it["status"]} tid={it["tid"]} '
                  f'{it["title"] or it["orig_title"][:50]}')
    elif args.cmd == "stats":
        print(json.dumps({"counts": db.counts(), "published_today": db.published_today()},
                         ensure_ascii=False))
    elif args.cmd == "serve":
        from web.app import serve
        print(f"管理台: http://{args.host}:{args.port}")
        serve(host=args.host, port=args.port)
    elif args.cmd == "mcp":
        import mcp_server
        mcp_server.main()


if __name__ == "__main__":
    main()
