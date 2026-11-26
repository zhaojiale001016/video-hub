"""uploader 核心分支逻辑: 平台选择/完成判定/版权/命令构造。"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import uploader  # noqa: E402


@pytest.fixture
def cfg(monkeypatch):
    """最小可用配置: extra_platforms 启用 kuaishou+tencent。"""
    c = {"settings": {"biliup_bin": "bin/biliup",
                      "biliup_cookie": "data/cookies.json"},
         "extra_platforms": {"kuaishou": {"enabled": True},
                             "tencent": {"enabled": True},
                             "douyin": {"enabled": False},
                             "bilibili": {"enabled": True}}}  # 保留字应被过滤
    return c


def _item(**kw):
    base = {"id": 1, "file_path": "/tmp/v.mp4", "title": "标题",
            "tags": "[]", "pub_platforms": "", "extra_pub": "",
            "bvid": "", "copyright": 0, "dtime": 0}
    base.update(kw)
    return base


# ---------- 平台选择 ----------

def test_selected_default_all(cfg):
    assert uploader.selected_platforms(_item()) is None  # None=跟随启用集


def test_selected_subset(cfg):
    it = _item(pub_platforms=json.dumps(["bilibili", "douyin"]))
    assert uploader.selected_platforms(it) == {"bilibili", "douyin"}


def test_selected_empty_list(cfg):
    it = _item(pub_platforms="[]")
    assert uploader.selected_platforms(it) == set()


def test_required_filters_disabled_and_reserved(cfg):
    # bilibili 在内置集合, douyin 未启用被过滤, 保留字 bilibili 在 ep 中不重复
    req = uploader.required_platforms(_item(), cfg)
    assert req == {"bilibili", "kuaishou", "tencent"}


def test_required_subset_intersect_enabled(cfg):
    it = _item(pub_platforms=json.dumps(["bilibili", "douyin", "kuaishou"]))
    req = uploader.required_platforms(it, cfg)
    assert req == {"bilibili", "kuaishou"}   # douyin 未启用不阻塞


# ---------- 完成判定(删除语义) ----------

def test_all_done_partial_fails(cfg):
    it = _item(extra_pub=json.dumps({
        "kuaishou": {"ok": True}, "tencent": {"ok": False}}))
    assert uploader.all_selected_done(it, cfg) is False


def test_all_done_full(cfg):
    it = _item(bvid="BV1xx", extra_pub=json.dumps({
        "kuaishou": {"ok": True}, "tencent": {"ok": True}}))
    assert uploader.all_selected_done(it, cfg) is True


def test_all_done_res_overrides_item(cfg):
    it = _item(bvid="BV1xx")
    res = {"kuaishou": {"ok": True}, "tencent": {"ok": True}}
    assert uploader.all_selected_done(it, cfg, res) is True


# ---------- 版权 ----------

def test_copyright_item_overrides_default(cfg):
    cfg["settings"]["default_copyright"] = 1
    assert uploader.effective_copyright(_item(copyright=2), cfg) == 2
    assert uploader.effective_copyright(_item(copyright=0), cfg) == 1
    assert uploader.effective_copyright(_item(copyright=0), {"settings": {}}) == 2


# ---------- 命令构造 ----------

def test_cmd_repost_needs_source(cfg):
    it = _item(copyright=2, source_url="https://youtu.be/x")
    cmd = uploader.build_upload_cmd(it, cfg)
    assert "--copyright" in cmd and "2" in cmd
    assert "--source" in cmd
    assert "--mission-id" not in cmd          # 转载禁活动


def test_cmd_original_with_mission(cfg):
    it = _item(copyright=1, mission_id=12345)
    cmd = uploader.build_upload_cmd(it, cfg)
    assert "--mission-id" in cmd


def test_cmd_past_dtime_dropped(cfg):
    it = _item(dtime=int(time.time()) - 600)
    assert "--dtime" not in uploader.build_upload_cmd(it, cfg)


def test_cmd_future_dtime_kept(cfg):
    it = _item(dtime=int(time.time()) + 5 * 3600)
    cmd = uploader.build_upload_cmd(it, cfg)
    assert "--dtime" in cmd


def test_cmd_cover_and_truncate(cfg):
    it = _item(cover_path="/tmp/c.jpg", title="x" * 200)
    cmd = uploader.build_upload_cmd(it, cfg)
    assert "--cover" in cmd
    assert len(cmd[cmd.index("--title") + 1]) == 80


# ---------- 单位换算 ----------

def test_to_bytes():
    assert uploader._to_bytes("100", "MB") == 100e6
    assert uploader._to_bytes("1", "GiB") == 2 ** 30
    assert uploader._to_bytes("512", "KiB") == 512 * 2 ** 10


# ---------- 删文件护栏 ----------

def test_delete_files_guard(tmp_path):
    # 约定布局 <dl>/<id>/x.mp4 → 整目录删
    d = tmp_path / "42"; d.mkdir(); (d / "v.mp4").write_text("x")
    uploader._delete_files({"id": 42, "file_path": str(d / "v.mp4")})
    assert not d.exists()
    # 非常规路径 → 只删文件不碰父目录
    parent = tmp_path / "keep"; parent.mkdir()
    f = parent / "v.mp4"; f.write_text("x")
    uploader._delete_files({"id": 1, "file_path": str(f)})
    assert parent.exists() and not f.exists()
