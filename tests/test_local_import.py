"""本地文件导入 + release-plugin 发版命令测试。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.db import DB  # noqa: E402
from pipeline import downloader  # noqa: E402
from cli import _release_plugin  # noqa: E402
import cli  # noqa: E402


def _cfg(tmp_path):
    """最小 cfg: download_dir 用 tmp 绝对路径, 不碰真实队列。"""
    dl = tmp_path / "dl"; dl.mkdir()
    return {"settings": {"download_dir": str(dl)},
            "sources": [], "subtitles": {"enabled": False}, "llm": {}}


@pytest.fixture
def env(tmp_path):
    return _cfg(tmp_path), DB(str(tmp_path / "t.db"))


def _vid(tmp_path, name="a.mp4"):
    p = tmp_path / name
    p.write_bytes(b"\x00" * 2048)   # 内容无所谓, 不跑 ffmpeg 真转码
    return str(p)


def test_import_copies_into_download_dir(env, tmp_path):
    cfg, db = env
    r = downloader.import_local_file(_vid(tmp_path), cfg, db, line="relax")
    assert r["ok"] and r["id"]
    it = db.get(r["id"])
    assert it["status"] == "new"
    assert it["platform"] == "local" and it["source_id"].startswith("local-")
    assert it["line"] == "relax" and it["copyright"] == 1
    fp = Path(it["file_path"])
    assert fp.exists() and fp.parent == Path(tmp_path / "dl" / str(r["id"]))
    # file_path 在约定布局内 → process_new_items 会跳过下载直接加工


def test_import_dedup(env, tmp_path):
    cfg, db = env
    v = _vid(tmp_path)
    assert downloader.import_local_file(v, cfg, db)["ok"]
    r2 = downloader.import_local_file(v, cfg, db)
    assert not r2["ok"] and "已导入" in r2["error"]


def test_import_rejects(env, tmp_path):
    cfg, db = env
    with pytest.raises(ValueError, match="不存在"):
        downloader.import_local_file("/no/such.mp4", cfg, db)
    bad = tmp_path / "x.txt"; bad.write_text("t")
    with pytest.raises(ValueError, match="类型"):
        downloader.import_local_file(str(bad), cfg, db)
    v = _vid(tmp_path, "b.mp4")
    with pytest.raises(ValueError, match="line"):
        downloader.import_local_file(v, cfg, db, line="Bad Line!")


def test_release_plugin_syncs_files(tmp_path, monkeypatch):
    pkg = tmp_path / "plugins" / "dsh-video-hub"; pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({
        "name": "dsh-video-hub", "version": "0.1.0",
        "engines": {"dsh": ">=0.2.0"}}))
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "dsh-index.json").write_text(json.dumps({
        "format": 1, "plugins": [{"name": "dsh-video-hub", "versions": [
            {"version": "0.1.0", "commit": "old"}]}]}))
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    r = _release_plugin("0.2.0", commit="abc12345")
    pkg = json.loads((pkg / "package.json").read_text())
    idx = json.loads((docs / "dsh-index.json").read_text())
    assert pkg["version"] == "0.2.0"
    vs = idx["plugins"][0]["versions"]
    assert vs[0]["version"] == "0.2.0" and vs[0]["source"]["commit"] == "abc12345"
    assert vs[0]["engines"] == {"dsh": ">=0.2.0"}
    assert len(vs) == 2 and vs[1]["version"] == "0.1.0"
    # 同版本重跑不重复条目
    _release_plugin("0.2.0", commit="def678")
    vs = json.loads((docs / "dsh-index.json").read_text())["plugins"][0]["versions"]
    assert [v["version"] for v in vs].count("0.2.0") == 1
    assert r["plugin"] == "0.1.0 → 0.2.0"


# ---------- cli.py release-plugin ----------

def test_release_plugin_bumps_pkg_and_index(tmp_path, monkeypatch):
    import cli
    plug_dir = tmp_path / "plugins" / "dsh-video-hub"
    plug_dir.mkdir(parents=True)
    (plug_dir / "package.json").write_text(
        '{"name": "dsh-video-hub", "version": "0.1.0", "engines": {"node": ">=22"}}')
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "dsh-index.json").write_text(
        '{"plugins": [{"name": "video-hub", "versions": [{"version": "0.1.0"}]}]}')
    monkeypatch.setattr(cli, "ROOT", tmp_path)

    r = cli._release_plugin("0.2.0", commit="deadbeef")

    import json as _j
    assert _j.loads((plug_dir / "package.json").read_text())["version"] == "0.2.0"
    idx = _j.loads((docs / "dsh-index.json").read_text())
    vs = idx["plugins"][0]["versions"]
    assert vs[0]["version"] == "0.2.0"
    assert vs[0]["source"]["commit"] == "deadbeef"
    assert vs[0]["source"]["path"] == "plugins/dsh-video-hub"
    assert vs[1]["version"] == "0.1.0"           # 旧版本保留
    assert "0.1.0 → 0.2.0" in r["plugin"]


def test_release_plugin_rejects_bad_version(tmp_path, monkeypatch):
    import cli, pytest
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        cli._release_plugin("latest")
