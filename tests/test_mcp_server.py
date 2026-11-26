"""mcp_server: 协议握手/工具表/调度 — 纯 stdio JSON-RPC, 不依赖 8600 服务。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mcp_server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def server():
    """起一个 stdio server 子进程, 喂 JSON-RPC。"""
    p = subprocess.Popen([sys.executable, str(ROOT / "mcp_server.py")],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True)
    yield p
    p.terminate()


def _rpc(p, method, params=None, i=1):
    req = {"jsonrpc": "2.0", "method": method, "id": i}
    if params is not None:
        req["params"] = params
    p.stdin.write(json.dumps(req) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())


def test_handshake_and_tools(server):
    r = _rpc(server, "initialize", {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"}})
    assert r["result"]["serverInfo"]["name"] == "video-hub"
    server.stdin.write(json.dumps(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    server.stdin.flush()
    r = _rpc(server, "tools/list", i=2)
    tools = r["result"]["tools"]
    assert len(tools) >= 15
    assert all("inputSchema" in t for t in tools)
    names = {t["name"] for t in tools}
    assert {"hub_stats", "hub_publish", "hub_edit", "hub_wait_job"} <= names


def test_unknown_method_and_tool(server):
    r = _rpc(server, "bogus/method", i=10)
    assert r["error"]["code"] == -32601
    r = _rpc(server, "tools/call", {"name": "nope", "arguments": {}}, i=11)
    assert r["error"]["code"] == -32602


def test_tool_graceful_error_no_api(server):
    """8600 没跑时工具返回 isError 而非崩协议。"""
    r = _rpc(server, "tools/call", {"name": "hub_stats", "arguments": {}}, i=20)
    res = r["result"]
    assert res["content"][0]["type"] == "text"
    body = json.loads(res["content"][0]["text"])
    assert "error" in body or "counts" in body     # 服务碰巧在跑也行


def test_tool_schemas_cover_required():
    """每个工具的 required 字段必须真实存在于 properties。"""
    for t in mcp_server.TOOLS:
        props = set(t["inputSchema"].get("properties", {}))
        for req in t["inputSchema"].get("required", []):
            assert req in props, f"{t['name']} required {req} 不在 properties"


def test_call_dispatch_bad_args():
    err, res = mcp_server._call_tool("hub_get", {"bad_arg": 1})
    assert err is not None or res["isError"]      # TypeError → -32602 或 isError
