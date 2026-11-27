"""dsh-video-hub 插件包测试: manifest/patch 结构 + node 沙箱验证工具注册。"""
import json
import shutil
import subprocess
from pathlib import Path

import yaml
import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "dsh-video-hub"
EXPECT_TOOLS = [
    "hub_stats", "hub_platforms", "hub_platform_login", "hub_platform_open",
    "hub_verify_code", "hub_progress", "hub_list", "hub_get", "hub_edit",
    "hub_publish", "hub_publish_all", "hub_poll", "hub_download", "hub_import", "hub_skip",
    "hub_retry", "hub_regen", "hub_delete_files", "hub_sources", "hub_add_source",
    "hub_jobs", "hub_job", "hub_wait_job",
]


@pytest.fixture(scope="module")
def pkg():
    return json.loads((PLUGIN / "package.json").read_text())


def test_package_manifest(pkg):
    assert pkg["name"] == "dsh-video-hub"
    assert pkg["type"] == "module"
    assert pkg["main"] == "index.js"
    assert (PLUGIN / pkg["main"]).exists()
    assert "dsh" in pkg["engines"]
    # 不依赖 dsh 私有 npm 包——纯 host 插件靠运行时注入
    assert "@deepseek-ai/dsh-tools" not in json.dumps(pkg.get("peerDependencies", {}))


def test_bundle_patch_declared(pkg):
    assert pkg["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert (PLUGIN / "cordis.patch.yml").exists()


def test_patch_inserts_plugin():
    patch = yaml.safe_load((PLUGIN / "cordis.patch.yml").read_text())
    assert isinstance(patch, list) and patch, "patch must be a non-empty op list"
    rows = [r for op in patch for r in (op.get("insert") or [])]
    row = next((r for r in rows if r.get("name") == "dsh-video-hub"), None)
    assert row, "patch must insert the plugin entry"
    assert row.get("id"), "entry needs a stable id for user overrides"
    assert row["config"]["api"].startswith("http://127.0.0.1"), "default must stay localhost"


def test_all_tools_present():
    src = (PLUGIN / "index.js").read_text()
    for t in EXPECT_TOOLS:
        assert f"name: '{t}'" in src, t
    for ex in ["export const name", "export const inject", "export function apply"]:
        assert ex in src, ex
    assert "ctx.tools.register" in src


@pytest.mark.skipif(not shutil.which("node"), reason="node 不在 PATH")
def test_tools_register_in_node(tmp_path):
    """真实执行 index.js: mock ctx.tools.register, 验证 19 工具注册+离线错误文案。"""
    script = tmp_path / "t.mjs"
    script.write_text(
        "import { apply, inject, name } from '%s/index.js'\n"
        "const reg = []; const ctx = { tools: { register: t => { reg.push(t); return () => {} } }, logger: {} }\n"
        "apply(ctx, { api: 'http://127.0.0.1:1' })\n"
        "console.log(JSON.stringify({ name, inject, tools: reg.map(t => t.name) }))\n"
        "const out = await reg[0].execute({})\n"
        "console.log('EXEC:' + JSON.stringify(JSON.parse(out).error ? 'err-ok' : 'err-missing'))\n"
        % PLUGIN.as_posix()
    )
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout.splitlines()[0])
    assert data["name"] == "dsh-video-hub"
    assert data["inject"] == ["tools"]
    assert data["tools"] == EXPECT_TOOLS
    assert 'EXEC:"err-ok"' in r.stdout, "离线时应返回含 error 的 JSON 而非抛异常"
