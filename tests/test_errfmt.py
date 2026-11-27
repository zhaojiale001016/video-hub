"""errfmt.summarize: 子进程原始输出 → UI 可读单行原因。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import errfmt


def test_login_expired_classified():
    out = "INFO: upload start\nERROR: please login again, SESSDATA invalid\n"
    assert errfmt.summarize(out).startswith("登录失效")


def test_risk_control_classified():
    assert "风控" in errfmt.summarize("检测到人机验证, captcha required")


def test_timeout_classified():
    assert "超时" in errfmt.summarize("TimeoutError: timed out after 30s")


def test_generic_uses_last_meaningful_line():
    out = ("Traceback (most recent call last):\n"
           "  File \"x.py\", line 1, in <m>\n"
           "ValueError: boom something broke")
    s = errfmt.summarize(out)
    assert "boom something broke" in s
    assert "Traceback" not in s


def test_empty_and_ansi():
    assert errfmt.summarize("") == "未知错误(无输出)"
    assert "\x1b" not in errfmt.summarize("\x1b[31mERR plain\x1b[0m")


def test_length_cap():
    s = errfmt.summarize("x" * 999, limit=100)
    assert len(s) <= 100
