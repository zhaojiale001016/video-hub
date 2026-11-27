"""封面亮度评分: 黑屏帧识别。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import downloader  # noqa: E402


def test_luminance_black_frame(tmp_path):
    from PIL import Image
    p = tmp_path / "b.png"
    Image.new("RGB", (64, 64), (0, 0, 0)).save(p)
    assert downloader._frame_luminance(p) < 20


def test_luminance_bright_frame(tmp_path):
    from PIL import Image
    p = tmp_path / "w.png"
    Image.new("RGB", (64, 64), (200, 200, 200)).save(p)
    assert downloader._frame_luminance(p) > 150


def test_luminance_missing_file(tmp_path):
    assert downloader._frame_luminance(tmp_path / "nope.png") == 0.0
