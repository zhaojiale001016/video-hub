"""precheck 标题相似度: 归一化 + bigram 抗重排。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import precheck  # noqa: E402


def test_norm_strips_decoration():
    assert precheck._norm("【精选】示例视频！✨") == precheck._norm("精选示例视频")


def test_sim_identical_after_norm():
    assert precheck._sim("✨高压清洗设备 全过程", "高压清洗设备全过程") > 0.9


def test_sim_reorder_caught():
    # 语序重排: 纯编辑距离会低, bigram Jaccard 兜住
    s = precheck._sim("示例视频完整过程", "完整过程示例视频")
    assert s > 0.6


def test_sim_unrelated_low():
    assert precheck._sim("示例短视频", "完全无关的另一主题") < 0.4


def test_sim_empty():
    assert precheck._sim("", "x") == 0.0
    assert precheck._sim("x", "") == 0.0
