"""集成级测试: 真实 FastAPI TestClient + 临时 sqlite, 覆盖端点冒烟、
状态机流转分支、错误边界 —— 单测之外的链路面。"""
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """TestClient + 隔离的临时 db。"""
    from pipeline.db import DB
    import web.app as webapp
    tmpdb = DB(str(tmp_path / "t.db"))
    monkeypatch.setattr(webapp, "db", tmpdb)
    from fastapi.testclient import TestClient
    return TestClient(webapp.app), tmpdb


def _ins(db, **kw):
    return db.insert_item({"source_name": "s", "line": "test", "platform": "yt",
                           "source_id": f"s{time.time_ns()}",
                           "source_url": "https://x/v", "orig_title": "t", **kw})


# ---------- 端点冒烟 ----------

def test_index_shell_integrity(app_client):
    """回归: 页面外壳完好——无孤儿 <style>/<script> 开标签吞文档, 外部资源引用在。"""
    c, _ = app_client
    r = c.get("/")
    assert r.status_code == 200
    h = r.text
    for t in ("style", "script", "body", "html"):
        assert h.count("<" + t) <= h.count("</" + t + ">") or h.count(f"<{t} ") == 0, \
            f"孤儿 <{t}> 开标签(会把后续 HTML 吞成文本导致页面空白)"
    assert 'src="/static/app.js"' in h
    assert 'href="/static/style.css"' in h


def test_static_assets_served(app_client):
    """拆分后的 css/js 可访问且非空。"""
    c, _ = app_client
    for u in ("/static/app.js", "/static/style.css"):
        r = c.get(u)
        assert r.status_code == 200 and len(r.content) > 1000, u

def test_api_stats_shape(app_client):
    c, db = app_client
    _ins(db)
    r = c.get("/api/stats")
    assert r.status_code == 200
    d = r.json()
    assert "counts" in d and "published_today" in d


def test_api_platforms_dict_shape(app_client):
    """回归: /api/platforms 必须是 dict(前端 Object.entries 依赖), 含富字段。"""
    c, _ = app_client
    d = c.get("/api/platforms").json()
    assert isinstance(d, dict)
    assert "bilibili" in d and "kuaishou" in d
    for k in ("name", "enabled", "logged_in", "ok_count", "fail_count", "links"):
        assert k in d["bilibili"]


def test_api_platform_open_rejects_unknown(app_client):
    c, _ = app_client
    assert c.post("/api/platforms/not-a-platform/open").status_code == 404


def test_api_items_list_and_patch(app_client):
    c, db = app_client
    iid = _ins(db)
    db.update(iid, status="ready", file_path="v.mp4")
    assert any(i["id"] == iid for i in c.get("/api/items").json()["items"])
    r = c.patch(f"/api/items/{iid}", json={"title": "改名"})
    assert r.status_code == 200
    assert db.get(iid)["title"] == "改名"


def test_api_item_404(app_client):
    c, _ = app_client
    assert c.patch("/api/items/999999", json={"title": "x"}).status_code == 404


# ---------- 状态机流转 ----------

def test_status_flow_happy_path(app_client):
    """new → downloaded → ready → published 全链。"""
    c, db = app_client
    iid = _ins(db)
    assert db.get(iid)["status"] == "new"
    db.set_status(iid, "downloaded")
    db.update(iid, file_path="v.mp4")
    db.set_status(iid, "ready")
    db.mark_published(iid, "BV1")
    it = db.get(iid)
    assert it["status"] == "published" and it["bvid"] == "BV1"


def test_extra_pub_partial_keeps_ledger(app_client):
    """一条腿成功一条腿失败 → extra_pub 逐腿独立记账、计数不错位。"""
    c, db = app_client
    iid = _ins(db)
    db.update(iid, status="published", bvid="BV1", file_path="v.mp4",
              published_at=int(time.time()),
              extra_pub=json.dumps({"douyin": {"ok": False, "msg": "风控"},
                                    "kuaishou": {"ok": True}}))
    res = json.loads(db.get(iid)["extra_pub"])
    assert res["douyin"]["ok"] is False and res["kuaishou"]["ok"] is True
    counts = db.platform_pub_counts()
    assert counts["kuaishou"] == [1, 0]
    assert counts["douyin"] == [0, 1]


def test_bvid_not_double_counted(app_client):
    """bvid 已计 B站, extra_pub.bilibili 不再重复计。"""
    c, db = app_client
    iid = _ins(db)
    db.update(iid, status="published", bvid="BV1", published_at=int(time.time()),
              extra_pub=json.dumps({"bilibili": {"ok": True}, "douyin": {"ok": True}}))
    counts = db.platform_pub_counts()
    assert counts["bilibili"] == [1, 0]   # bvid 计一次, extra_pub.bilibili 不重复计
    assert counts["douyin"] == [1, 0]


def test_skip_unskip_roundtrip(app_client):
    c, db = app_client
    iid = _ins(db)
    db.update(iid, status="ready", file_path="nonexist.mp4")
    assert c.post(f"/api/items/{iid}/skip").status_code == 200
    assert db.get(iid)["status"] == "skipped"
    assert c.post(f"/api/items/{iid}/unskip").status_code == 200
    assert db.get(iid)["status"] == "new"  # unskip 回 new 重新走下载


# ---------- 错误边界 ----------

def test_patch_published_rejected(app_client):
    """已发布稿件禁止编辑(防与线上脱节)——状态机保护分支。"""
    c, db = app_client
    iid = _ins(db)
    db.update(iid, status="published", bvid="BV1")
    assert c.patch(f"/api/items/{iid}", json={"title": "x"}).status_code == 400


def test_unskip_requires_skipped(app_client):
    """非 skipped 条目 unskip → 400(状态机防漏分支)。"""
    c, db = app_client
    iid = _ins(db)
    assert c.post(f"/api/items/{iid}/unskip").status_code == 400
