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


def _make_video(dst: Path, black_secs: float = 0.0, body_secs: float = 1.0):
    """黑场+白场拼接测试视频(需 ffmpeg, 无则跳过)。"""
    import shutil
    import subprocess
    import pytest
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    if black_secs > 0:
        r = subprocess.run(
            ["ffmpeg", "-y",
             "-f", "lavfi", "-i", f"color=black:s=64x64:d={black_secs}",
             "-f", "lavfi", "-i", f"color=white:s=64x64:d={body_secs}",
             "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
             "-map", "[v]", str(dst)], capture_output=True)
    else:
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"color=white:s=64x64:d={body_secs}", str(dst)],
            capture_output=True)
    assert r.returncode == 0 and dst.exists(), r.stderr.decode()[-300:]


def test_leading_black_detect(tmp_path):
    v = tmp_path / "v.mp4"
    _make_video(v, black_secs=1.5, body_secs=1.5)
    end = downloader._leading_black_end(str(v))
    assert 1.0 < end < 3.0


def test_leading_black_none(tmp_path):
    v = tmp_path / "v.mp4"
    _make_video(v, black_secs=0.0, body_secs=1.5)
    assert downloader._leading_black_end(str(v)) == 0.0


def test_trim_leading_black(tmp_path):
    v = tmp_path / "video.mp4"
    _make_video(v, black_secs=1.5, body_secs=2.0)
    cut = downloader.trim_leading_black(str(v))
    assert 1.0 < cut < 3.0
    assert v.exists() and not (v.parent / "video_trimmed.mp4").exists()
    # 修剪后开头不再黑
    assert downloader._leading_black_end(str(v)) < 0.4


def test_trim_skips_tiny_black(tmp_path):
    v = tmp_path / "video.mp4"
    _make_video(v, black_secs=0.2, body_secs=1.5)   # <0.4s 不值得重编码
    assert downloader.trim_leading_black(str(v)) == 0.0


def _mk_items(spec: dict[str, int]) -> list[dict]:
    """{line: 条数} → id 递减的假条目列表。"""
    i, out = 100, []
    for ln, n in spec.items():
        for _ in range(n):
            out.append({"id": i, "line": ln})
            i -= 1
    return out


def test_interleave_round_robin():
    items = _mk_items({"a": 3, "b": 3, "c": 3})
    got = [it["line"] for it in downloader._interleave_by_line(items)]
    assert got == ["a", "b", "c"] * 3


def test_interleave_uneven_lines():
    items = _mk_items({"a": 4, "b": 1, "c": 2})
    got = [it["line"] for it in downloader._interleave_by_line(items)]
    assert got[:3] == ["a", "b", "c"]          # 首轮每线各一
    assert got.count("b") == 1 and got.count("c") == 2
    assert len(got) == 7                        # 耗尽线自然让位


def test_interleave_preserves_intra_order():
    items = [{"id": 5, "line": "a"}, {"id": 3, "line": "a"},
             {"id": 4, "line": "b"}]
    got = [(it["line"], it["id"]) for it in downloader._interleave_by_line(items)]
    assert got == [("a", 5), ("b", 4), ("a", 3)]   # 线内保持原序


def test_interleave_missing_line_key():
    items = [{"id": 1}, {"id": 2, "line": "x"}]
    got = downloader._interleave_by_line(items)
    assert len(got) == 2                          # 无线名归 default, 不丢
