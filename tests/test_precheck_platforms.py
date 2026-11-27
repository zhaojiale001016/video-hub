"""多平台撞稿预检: targets 过滤 / platform 标记 / 无搜索平台跳过 / B站代理探测。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import precheck, missions


@pytest.fixture()
def cfg():
    return {"sources_file": "config/sources.example.yaml"}


def _patch(monkeypatch, bili_hit=None, yt_hit=None, sessdata=True):
    monkeypatch.setattr(missions, "_sessdata", lambda c: sessdata)
    calls = []

    def fake_bili(c, q, k, mid, bvs):
        calls.append("bilibili")
        return bili_hit

    def fake_yt(c, q, k, mid, bvs):
        calls.append("youtube")
        return yt_hit

    monkeypatch.setattr(precheck, "_BACKENDS",
                        {"bilibili": fake_bili, "youtube": fake_yt})
    return calls


HIT = {"bvid": "BV9", "title": "撞车稿", "author": "x", "sim": 0.9}


def test_targets_only_searched(cfg, monkeypatch):
    """只勾选抖音 → B站有登录态时仍作代理探测, YouTube 不搜。"""
    calls = _patch(monkeypatch, bili_hit=HIT)
    hit = precheck.check_collision(cfg, {"title": "t", "orig_title": "o"},
                                   targets={"douyin"})
    assert calls == ["bilibili"]
    assert hit["platform"] == "bilibili"


def test_youtube_searched_when_targeted(cfg, monkeypatch):
    calls = _patch(monkeypatch, yt_hit=HIT, sessdata=False)
    hit = precheck.check_collision(cfg, {"title": "t", "orig_title": "o"},
                                   targets={"youtube", "douyin"})
    assert calls == ["youtube"]
    assert hit["platform"] == "youtube"


def test_no_sessdata_skips_bilibili(cfg, monkeypatch):
    """未勾B站且无登录态 → 无可搜平台 → 不撞。"""
    calls = _patch(monkeypatch, sessdata=False)
    assert precheck.check_collision(
        cfg, {"title": "t", "orig_title": "o"}, targets={"douyin"}) is None
    assert calls == []


def test_nonsearchable_never_called(cfg, monkeypatch):
    """douyin/kuaishou/xhs/tencent 无公开搜索 → 永远不进后端。"""
    calls = _patch(monkeypatch, sessdata=False)
    precheck.check_collision(cfg, {"title": "t", "orig_title": "o"},
                             targets={"douyin", "kuaishou", "xiaohongshu"})
    assert calls == []


def test_below_threshold_passes(cfg, monkeypatch):
    _patch(monkeypatch, bili_hit={**HIT, "sim": 0.5})
    assert precheck.check_collision(cfg, {"title": "t", "orig_title": "o"},
                                    targets={"bilibili"}, threshold=0.72) is None
