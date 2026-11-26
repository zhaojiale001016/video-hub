"""config: 深合并/覆盖层/原子写。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config as C  # noqa: E402


def test_deep_merge_nested():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    C._deep_merge(base, {"a": {"y": 9, "z": 4}})
    assert base == {"a": {"x": 1, "y": 9, "z": 4}, "b": 3}


def test_get_setting_default():
    cfg = {"settings": {"x": 5}}
    assert C.get_setting(cfg, "x") == 5
    assert C.get_setting(cfg, "missing", 42) == 42
    assert C.get_setting({"settings": None}, "x", 7) in (None, 7)


def test_update_overrides_atomic(tmp_path, monkeypatch):
    ov = tmp_path / "overrides.json"
    monkeypatch.setattr(C, "OVERRIDES", ov)
    C.update_overrides(lambda o: o.setdefault("settings", {}) .update(a=1))
    C.update_overrides(lambda o: o["settings"].update(b=2))
    data = json.loads(ov.read_text())
    assert data["settings"] == {"a": 1, "b": 2}
    assert not ov.with_suffix(".tmp").exists()   # tmp 已 rename


def test_load_overrides_broken_json(tmp_path, monkeypatch):
    ov = tmp_path / "overrides.json"
    ov.write_text("{broken")
    monkeypatch.setattr(C, "OVERRIDES", ov)
    assert C.load_overrides() == {}
