"""multipub: 平台注册表/能力矩阵/命令构造/登录态。"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import multipub  # noqa: E402


@pytest.fixture
def cfg():
    return {"settings": {},
            "extra_platforms": {"kuaishou": {"enabled": True},
                                "tencent": {"enabled": True},
                                "douyin": {"enabled": False},
                                "bilibili": {"enabled": True},   # 保留字
                                "article": {"enabled": True}}}   # 保留字


def _item(**kw):
    base = {"id": 1, "file_path": "/tmp/v.mp4", "title": "t",
            "orig_title": "o", "description": "d", "tags": '["a","b"]',
            "cover_path": "/tmp/c.jpg", "dtime": 0}
    base.update(kw)
    return base


def _cmd_for(platform, item=None, cfg=None):
    """捕获 publish_to 实际生成的 sau_cli 命令(mock 子进程)。"""
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        class R:  # noqa: D401 - 微型桩
            returncode = 0
            stdout = "ok"
            stderr = ""
        return R()
    with patch.object(multipub.subprocess, "run", fake_run):
        multipub.publish_to(platform, item or _item(), cfg or {"settings": {}})
    return captured["cmd"]


# ---------- 注册表健康 ----------

def test_registry_no_reserved():
    assert not (set(multipub.SAU_PLATFORMS) & multipub._RESERVED)
    assert set(multipub.SAU_PLATFORMS) <= set(multipub.PLATFORMS)


def test_capability_matrices_subset():
    known = set(multipub.SAU_PLATFORMS) | {"bilibili"}
    for m in (multipub._NO_THUMB, multipub._NO_TAGS,
              multipub._NO_SCHEDULE, multipub._LAND_THUMB):
        assert m <= known, f"能力矩阵含未知平台: {m - known}"


def test_enabled_filters_reserved_and_disabled(cfg):
    got = multipub.enabled_platforms(cfg)
    assert got == ["kuaishou", "tencent"]


# ---------- 平台 id 校验 ----------

@pytest.mark.parametrize("pid,ok", [
    ("kuaishou", True), ("facebook-page", True), ("x_y2", True),
    ("bilibili", False), ("article", False), ("../evil", False),
    ("Douyin", False), ("a" * 25, False), ("-x", False), ("", False),
])
def test_valid_platform_id(pid, ok):
    assert (multipub.valid_platform_id(pid) is None) == ok


# ---------- 命令构造(能力矩阵) ----------

def test_zhihu_no_tags_no_thumb(cfg):
    cmd = _cmd_for("zhihu", cfg=cfg)
    assert "--tags" not in cmd and "--thumbnail" not in cmd
    assert "--thumbnail-landscape" not in cmd


def test_tencent_landscape_thumb(cfg):
    cmd = _cmd_for("tencent", cfg=cfg)
    assert "--thumbnail-landscape" in cmd and "--thumbnail" not in cmd


def test_douyin_schedule(cfg):
    it = _item(dtime=int(time.time()) + 5 * 3600)
    cmd = _cmd_for("douyin", item=it, cfg=cfg)
    assert "--schedule" in cmd
    cmd2 = _cmd_for("weibo", item=it, cfg=cfg)
    assert "--schedule" not in cmd2       # weibo 无定时能力


def test_past_dtime_no_schedule(cfg):
    cmd = _cmd_for("douyin", item=_item(dtime=int(time.time()) - 60), cfg=cfg)
    assert "--schedule" not in cmd


def test_generic_platform_thumb_and_tags(cfg):
    cmd = _cmd_for("youtube", cfg=cfg)
    assert "--thumbnail" in cmd and "--tags" in cmd


def test_title_limit_applied(cfg):
    cmd = _cmd_for("kuaishou", item=_item(title="x" * 100), cfg=cfg)
    assert len(cmd[cmd.index("--title") + 1]) == 30   # kuaishou 上限 30


def _env_for(platform, cfg=None):
    captured = {}
    def fake_run(cmd, **kw):
        captured["env"] = kw.get("env")
        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return R()
    with patch.object(multipub.subprocess, "run", fake_run):
        multipub.publish_to(platform, _item(), cfg or {"settings": {}})
    return captured["env"]


def test_tencent_label_env_default_skip():
    env = _env_for("tencent")
    assert env and env["SAU_TENCENT_CONTENT_LABEL"] == "none"


def test_platform_meta_all_have_home():
    """中枢面板依赖: 全部平台(含 B站)至少要有 home 直达链接。"""
    for pid in ["bilibili"] + multipub.SAU_PLATFORMS:
        meta = multipub.PLATFORM_META.get(pid) or {}
        assert meta.get("home"), f"{pid} 缺 home 链接"
        assert meta.get("icon"), f"{pid} 缺图标"


def test_open_creator_errors(cfg, monkeypatch):
    """无 meta/无 cookie 的平台要干净报错, 不拉起子进程。"""
    ok, msg = multipub.open_creator(cfg, "no-such-platform", "home")
    assert not ok and "链接" in msg
    monkeypatch.setattr(multipub, "_cookie_file", lambda *a: None)
    ok, msg = multipub.open_creator(cfg, "kuaishou", "home")
    assert not ok and "登录态" in msg


def test_open_creator_target_fallback(cfg, monkeypatch):
    """未知 target 兜底回 home; 拉起命令含 home 链接与 cookie。"""
    monkeypatch.setattr(multipub, "_cookie_file", lambda *a: Path("/tmp/ck.json"))
    spawned = {}
    def fake_popen(cmd, **kw):
        spawned["cmd"], spawned["kw"] = cmd, kw
    monkeypatch.setattr(multipub.subprocess, "Popen", fake_popen)
    ok, url = multipub.open_creator(cfg, "kuaishou", "bogus")
    assert ok and url == multipub.PLATFORM_META["kuaishou"]["home"]
    assert multipub.PLATFORM_META["kuaishou"]["home"] in spawned["cmd"]
    assert "/tmp/ck.json" in spawned["cmd"] and spawned["kw"]["start_new_session"]


def test_tencent_label_env_override():
    env = _env_for("tencent", {"settings": {"tencent_content_label": "含AI生成内容"}})
    assert env["SAU_TENCENT_CONTENT_LABEL"] == "含AI生成内容"


def test_non_tencent_no_label_env():
    env = _env_for("kuaishou")
    assert env is not None                        # 现在有 SAU_DEBUG_DIR
    assert "SAU_TENCENT_CONTENT_LABEL" not in env  # 标注 env 仅腾讯专属
    assert env["SAU_DEBUG_DIR"].endswith("data/debug")


def test_tencent_dual_covers(tmp_path, monkeypatch):
    p = tmp_path / "cp.jpg"; p.write_bytes(b"x")
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return R()
    it = _item()
    it["cover_portrait_path"] = str(p)
    with patch.object(multipub.subprocess, "run", fake_run):
        multipub.publish_to("tencent", it, {"settings": {}})
    cmd = captured["cmd"]
    assert "--thumbnail-landscape" in cmd and "--thumbnail-portrait" in cmd
    assert cmd[cmd.index("--thumbnail-portrait") + 1] == str(p)


def test_kuaishou_prefers_portrait(tmp_path):
    p = tmp_path / "cp.jpg"; p.write_bytes(b"x")
    it = _item(); it["cover_portrait_path"] = str(p)
    cmd = _cmd_for("kuaishou", it)
    assert cmd[cmd.index("--thumbnail") + 1] == str(p)


def test_kuaishou_fallback_landscape():
    assert "--thumbnail" in _cmd_for("kuaishou")   # 无竖封仍走横图


def test_douyin_no_portrait_no_param():
    assert "--thumbnail-portrait" not in _cmd_for("douyin")


def test_portrait_matrix_subset():
    assert multipub._PORTRAIT_THUMB <= set(multipub.SAU_PLATFORMS)
