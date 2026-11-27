"""DB 层: 幂等入库/状态机/日计数。"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.db import DB  # noqa: E402


@pytest.fixture
def db(tmp_path):
    return DB(str(tmp_path / "t.db"))


def _add(db, sid="s1", plat="youtube"):
    return db.insert_item({"platform": plat, "source_id": sid,
                           "source_url": "u", "line": "vlog",
                           "orig_title": "o"})


def test_insert_idempotent(db):
    i1 = _add(db)
    assert i1 is not None
    assert _add(db) is None                      # UNIQUE(platform,source_id) 拦截
    assert _add(db, sid="s2") is not None        # 不同 source_id 可入
    assert len(db.list_items(status="new", limit=10)) == 2


def test_status_flow_and_counts(db):
    i = _add(db)
    db.set_status(i, "ready")
    db.mark_published(i, "BV1xx", "ok")
    it = db.get(i)
    assert it["status"] == "published" and it["bvid"] == "BV1xx"
    assert db.published_today() == 1
    assert db.counts()["published"] == 1


def test_platform_pub_counts(db):
    import json
    i = _add(db)
    db.mark_published(i, "BV1xx", "ok")
    db.update(i, extra_pub=json.dumps(
        {"kuaishou": {"ok": True}, "douyin": {"ok": False},
         "bilibili": {"ok": True, "bvid": "BV1xx"}}))
    i2 = _add(db, sid="s2")
    db.update(i2, extra_pub=json.dumps({"kuaishou": {"ok": True}}))
    c = db.platform_pub_counts()
    assert c["bilibili"] == [1, 0]            # bvid 计成功, extra_pub 去重不重复计
    assert c["kuaishou"] == [2, 0]
    assert c["douyin"] == [0, 1]


def test_downloaded_today(db):
    i = _add(db)
    db.update(i, downloaded_at=int(time.time()))
    assert db.downloaded_today() == 1


def test_multi_status_query(db):
    i = _add(db)
    db.set_status(i, "failed")
    rows = db.list_items(status="ready,failed", limit=10)
    assert [r["id"] for r in rows] == [i]


def test_update_arbitrary_fields(db):
    i = _add(db)
    db.update(i, title="新标题", dtime=123)
    it = db.get(i)
    assert it["title"] == "新标题" and it["dtime"] == 123
