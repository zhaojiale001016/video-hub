"""precheck 标题相似度: 归一化 + bigram 抗重排。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import precheck  # noqa: E402


def test_norm_strips_decoration():
    assert precheck._norm("【解压】洗地毯！✨") == precheck._norm("解压洗地毯")


def test_sim_identical_after_norm():
    assert precheck._sim("【4K】高压水枪洗地毯 全过程", "高压水枪洗地毯全过程") > 0.9


def test_sim_reorder_caught():
    # 语序重排: 纯编辑距离会低, bigram Jaccard 兜住
    s = precheck._sim("洗地毯全过程解压视频", "解压视频洗地毯全过程")
    assert s > 0.6


def test_sim_unrelated_low():
    assert precheck._sim("洗地毯解压", "荒野求生建庇护所") < 0.4


def test_sim_empty():
    assert precheck._sim("", "x") == 0.0
    assert precheck._sim("x", "") == 0.0
