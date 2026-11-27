"""video-hub MCP server — 让 AI 工具(Claude Code/Desktop, Cursor, Devin 等)
通过 MCP 协议直接驱动搬运中台。

实现方式: stdio JSON-RPC 2.0 薄封装本机 HTTP API(默认 127.0.0.1:8600)。
无第三方协议依赖(协议面很小, 直接实现)——只需要 httpx。
前置: 管理服务已在跑(`cli.py serve` / launchd 常驻)。

客户端配置示例(claude_desktop_config.json / .mcp.json / cursor mcp):
{
  "mcpServers": {
    "video-hub": {
      "command": "/path/to/video-hub/.venv/bin/python3",
      "args": ["/path/to/video-hub/mcp_server.py"],
      "env": {"VIDEOHUB_API": "http://127.0.0.1:8600"}
    }
  }
}
"""
from __future__ import annotations

import json
import os
import sys
import time

try:
    import httpx
except ImportError:
    sys.exit("缺 httpx: pip install httpx (或 pip install -r requirements.txt)")

API = os.environ.get("VIDEOHUB_API", "http://127.0.0.1:8600").rstrip("/")
PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "video-hub", "version": "1.5.0"}


def _req(method: str, path: str, timeout: float = 30, **kw):
    try:
        r = httpx.request(method, f"{API}{path}", timeout=timeout, **kw)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        return {"error": f"管理服务未运行({API})——先 `cli.py serve` 或确认 launchd 常驻"}
    except httpx.HTTPStatusError as e:
        try:
            return {"error": e.response.json().get("detail", str(e))}
        except Exception:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": str(e)[:300]}


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=1, default=str)


# ---------- 工具实现 ----------

def _hub_stats():
    return _req("GET", "/api/stats")


def _hub_platforms():
    return _req("GET", "/api/platforms")


def _hub_platform_login(pid):
    return _req("POST", f"/api/platforms/{pid}/login")


def _hub_platform_open(pid, target="home"):
    return _req("POST", f"/api/platforms/{pid}/open",
                json={"target": target})


def _hub_verify_code(code):
    return _req("POST", "/api/verify-code", json={"code": str(code)})


def _hub_progress():
    return _req("GET", "/api/progress")


def _hub_list(status="ready", line="", limit=50):
    return _req("GET", "/api/items",
                params={"status": status or None, "line": line or None,
                        "limit": limit})


def _hub_get(item_id):
    for st in ("ready", "downloaded", "new", "published", "failed", "skipped"):
        data = _req("GET", "/api/items", params={"status": st, "limit": 10 ** 6})
        for it in data.get("items", []):
            if it["id"] == item_id:
                return it
    return {"error": f"#{item_id} 不存在"}


def _hub_edit(item_id, title="", description="", tags=None, tid=None,
              mission_id=None, copyright=None, dtime=None, pub_platforms=None):
    body = {}
    if title:
        body["title"] = title
    if description:
        body["description"] = description
    if tags is not None:
        body["tags"] = tags
    for k, v in (("tid", tid), ("mission_id", mission_id),
                 ("copyright", copyright), ("dtime", dtime),
                 ("pub_platforms", pub_platforms)):
        if v is not None:
            body[k] = v
    if not body:
        return {"error": "没有任何字段要改"}
    return _req("PATCH", f"/api/items/{item_id}", json=body)


def _hub_publish(item_id, force=False, dtime=0):
    return _req("POST", f"/api/items/{item_id}/publish",
                params={"force": force, "dtime": dtime}, timeout=15)


def _hub_publish_all(schedule=False):
    return _req("POST", "/api/publish-all",
                params={"schedule": schedule}, timeout=15)


def _hub_poll():
    return _req("POST", "/api/poll", timeout=15)


def _hub_download(item_id=0, limit=0):
    if item_id:
        return _req("POST", f"/api/items/{item_id}/download", timeout=15)
    return _req("POST", "/api/download", params={"limit": limit}, timeout=15)


def _hub_import(path, line="local", title="", process=True):
    return _req("POST", "/api/import", timeout=15, json={
        "path": path, "line": line, "title": title, "process": process})


def _hub_skip(item_id, undo=False):
    return _req("POST", f"/api/items/{item_id}/{'unskip' if undo else 'skip'}")


def _hub_retry(item_id):
    return _req("POST", f"/api/items/{item_id}/retry")


def _hub_regen(item_id):
    return _req("POST", f"/api/items/{item_id}/regen", timeout=120)


def _hub_delete_files(item_id):
    return _req("DELETE", f"/api/items/{item_id}/files")


def _hub_sources():
    return _req("GET", "/api/sources")


def _hub_add_source(name, url, line, kind="channel", tid=0, limit=0,
                    subtitle=""):
    return _req("POST", "/api/sources", json={
        "name": name, "url": url, "line": line, "kind": kind,
        "tid": tid, "limit": limit, "subtitle": subtitle})


def _hub_jobs():
    return _req("GET", "/api/jobs")


def _hub_job(job_id):
    return _req("GET", f"/api/jobs/{job_id}")


def _hub_wait_job(job_id, timeout_s=120):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        j = _req("GET", f"/api/jobs/{job_id}")
        if isinstance(j, dict) and j.get("status") in ("done", "error"):
            return j
        time.sleep(3)
    return {"error": f"{timeout_s}s 内未结束", "job_id": job_id}


# ---------- 工具表 ----------

_S = {"type": "string"}
_I = {"type": "integer"}
_B = {"type": "boolean"}
_A = {"type": "array", "items": {"type": "string"}}

TOOLS = [
    {"name": "hub_stats", "fn": _hub_stats,
     "description": "队列计数/今日额度/磁盘/各平台登录态/LLM开关",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hub_platforms", "fn": _hub_platforms,
     "description": "全部可投递平台: 名称/启用态/builtin(B站)/custom",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hub_platform_login", "fn": _hub_platform_login,
     "description": "拉起指定 sau 平台的扫码登录浏览器(有头, 需本机有屏幕; B站走 QR API 不用此)",
     "inputSchema": {"type": "object", "properties": {
         "pid": _S}, "required": ["pid"]}},
    {"name": "hub_platform_open", "fn": _hub_platform_open,
     "description": "带 sau 登录态拉起真实浏览器直达创作平台页(target: home/manage/publish); 有头, 需本机屏幕",
     "inputSchema": {"type": "object", "properties": {
         "pid": _S, "target": _S}, "required": ["pid"]}},
    {"name": "hub_verify_code", "fn": _hub_verify_code,
     "description": "抖音等平台投递触发短信/滑块验证时提交 4-8 位数字验证码(写入 sau 拾取文件, 等待中的进程自动续跑)",
     "inputSchema": {"type": "object", "properties": {
         "code": _S}, "required": ["code"]}},
    {"name": "hub_progress", "fn": _hub_progress,
     "description": "正在处理的条目实时进度(下载MB/上传%/烧录阶段)",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hub_list", "fn": _hub_list,
     "description": "列条目。status: new/downloaded/ready/published/failed/skipped(逗号可组合); line 按内容线过滤",
     "inputSchema": {"type": "object", "properties": {
         "status": _S, "line": _S, "limit": _I}}},
    {"name": "hub_get", "fn": _hub_get,
     "description": "单条详情: 标题/简介/标签/extra_pub逐平台投递结果/文件路径",
     "inputSchema": {"type": "object", "properties": {
         "item_id": _I}, "required": ["item_id"]}},
    {"name": "hub_edit", "fn": _hub_edit,
     "description": "编辑稿件元数据。copyright: 1=自制 2=转载; pub_platforms 传None不改, 传子集显式圈定发布目标。已发布稿件不可改",
     "inputSchema": {"type": "object", "properties": {
         "item_id": _I, "title": _S, "description": _S, "tags": _A,
         "tid": _I, "mission_id": _I, "copyright": _I, "dtime": _I,
         "pub_platforms": _A}, "required": ["item_id"]}},
    {"name": "hub_publish", "fn": _hub_publish,
     "description": "发布一条: 勾选平台各自投递互不阻断, 已成功平台自动跳过。force 跳撞稿预检; dtime=10位时间戳(≥4h后)。返回 job_id",
     "inputSchema": {"type": "object", "properties": {
         "item_id": _I, "force": _B, "dtime": _I}, "required": ["item_id"]}},
    {"name": "hub_publish_all", "fn": _hub_publish_all,
     "description": "按每日限额把 ready 队列发完; schedule=True 按 publish_slots 错峰定时。返回 job_id",
     "inputSchema": {"type": "object", "properties": {"schedule": _B}}},
    {"name": "hub_poll", "fn": _hub_poll,
     "description": "轮询全部启用订阅源拉新入队(含撞稿预检)。返回 job_id",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hub_download", "fn": _hub_download,
     "description": "item_id>0 单条下载(不占日额度); 否则批量(受 daily_download_limit)。返回 job_id",
     "inputSchema": {"type": "object", "properties": {
         "item_id": _I, "limit": _I}}},
    {"name": "hub_import", "fn": _hub_import,
     "description": "导入本地视频文件进流水线(复制入下载目录, 默认自制版权)。process=True 异步字幕+元数据→ready 返回 job_id; False 只入库停 new",
     "inputSchema": {"type": "object", "properties": {
         "path": _S, "line": _S, "title": _S, "process": _B},
         "required": ["path"]}},
    {"name": "hub_skip", "fn": _hub_skip,
     "description": "跳过(删文件腾磁盘); undo=True 撤销并清 archive 允许重下",
     "inputSchema": {"type": "object", "properties": {
         "item_id": _I, "undo": _B}, "required": ["item_id"]}},
    {"name": "hub_retry", "fn": _hub_retry,
     "description": "失败条目重试: 文件在→ready; 文件没→new 清 archive 重下",
     "inputSchema": {"type": "object", "properties": {
         "item_id": _I}, "required": ["item_id"]}},
    {"name": "hub_regen", "fn": _hub_regen,
     "description": "LLM 重新生成标题/简介/标签/分区(已发布稿件不可)",
     "inputSchema": {"type": "object", "properties": {
         "item_id": _I}, "required": ["item_id"]}},
    {"name": "hub_delete_files", "fn": _hub_delete_files,
     "description": "清理已发布稿件本地文件(部分平台未投完请先补投——文件是重投材料)",
     "inputSchema": {"type": "object", "properties": {
         "item_id": _I}, "required": ["item_id"]}},
    {"name": "hub_sources", "fn": _hub_sources,
     "description": "全部订阅源+健康档案(最近轮询/新增/错误)",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hub_add_source", "fn": _hub_add_source,
     "description": "加订阅源。kind: channel/search; line: 任意小写内容线slug; subtitle 可覆盖线策略(always/auto/subs_only/never)",
     "inputSchema": {"type": "object", "properties": {
         "name": _S, "url": _S, "line": _S, "kind": _S,
         "tid": _I, "limit": _I, "subtitle": _S},
         "required": ["name", "url", "line"]}},
    {"name": "hub_jobs", "fn": _hub_jobs,
     "description": "近20个后台任务: job_id/状态/结果摘要",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hub_job", "fn": _hub_job,
     "description": "单任务详情+日志尾部",
     "inputSchema": {"type": "object", "properties": {
         "job_id": _S}, "required": ["job_id"]}},
    {"name": "hub_wait_job", "fn": _hub_wait_job,
     "description": "阻塞等任务结束(最长 timeout_s 秒)返回最终状态+结果",
     "inputSchema": {"type": "object", "properties": {
         "job_id": _S, "timeout_s": _I}, "required": ["job_id"]}},
]

_TOOL_MAP = {t["name"]: t for t in TOOLS}


# ---------- stdio JSON-RPC ----------

def _reply(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _call_tool(name: str, args: dict):
    t = _TOOL_MAP.get(name)
    if not t:
        return {"code": -32602, "message": f"unknown tool {name}"}, None
    try:
        res = t["fn"](**(args or {}))
        return None, {"content": [{"type": "text", "text": _dump(res)}],
                      "isError": isinstance(res, dict) and "error" in res}
    except TypeError as e:
        return {"code": -32602, "message": f"参数错误: {e}"}, None
    except Exception as e:
        return None, {"content": [{"type": "text",
                                   "text": _dump({"error": str(e)[:300]})}],
                      "isError": True}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, rid = req.get("method"), req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            _reply(rid, {"protocolVersion": PROTOCOL_VERSION,
                         "capabilities": {"tools": {}},
                         "serverInfo": SERVER_INFO,
                         "instructions": "视频搬运中台。长任务(poll/download/publish)返回job_id, "
                                         "用 hub_job/hub_wait_job 取结果。"})
        elif method == "ping":
            _reply(rid, {})
        elif method == "tools/list":
            _reply(rid, {"tools": [
                {k: t[k] for k in ("name", "description", "inputSchema")}
                for t in TOOLS]})
        elif method == "tools/call":
            err, res = _call_tool(params.get("name"), params.get("arguments"))
            if err:
                _reply(rid, error=err)
            else:
                _reply(rid, res)
        elif rid is not None:            # 未知方法+带id → -32601; 通知一律吞掉
            _reply(rid, error={"code": -32601, "message": f"method not found: {method}"})


if __name__ == "__main__":
    main()
