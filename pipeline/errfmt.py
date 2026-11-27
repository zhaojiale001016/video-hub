"""子进程/第三方错误摘要: 把原始 stderr/异常归类成一行可读原因进 UI。
日志里仍保留完整输出(调用方自行 log), 这里只管"给人看的那一行"。
"""
import re

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_NOISE = re.compile(
    r"^\s*(Traceback |File \"|  +\S+\s*$|await |raise |Deprecation|warnings\.warn)")
_RULES = [
    (r"(?i)(SESSDATA.*(invalid|expire)|登录.*(失效|过期)|not.?logged.?in|请.*登录|扫码|qr.?code.*expired|401|403.*login)",
     "登录失效"),
    (r"(?i)(风控|验证码|captcha|滑块|verify|risk|challenge|人机)", "风控/验证拦截"),
    (r"(?i)(timeout|timed.?out|超时)", "超时"),
    (r"(?i)(no space|disk.*full|errno 28)", "磁盘不足"),
    (r"(?i)(connection|refused|unreachable|network|reset by peer|DNS)", "网络错误"),
    (r"(?i)(sign.*(invalid|expired)|签名.*失效|cookie.*invalid)", "签名/凭证失效"),
    (r"(?i)(rate.?limit|too many|频繁|限流|429)", "限流"),
    (r"(?i)(copyright|版权|审核|review.*fail|驳回)", "平台审核拦截"),
]


def summarize(raw, limit: int = 200) -> str:
    """raw: 子进程合并输出或异常文本 → '归类: 关键行' 单行。
    无匹配归类时取最后一条有效行(跳过 traceback 噪音行)。"""
    text = _ANSI.sub("", str(raw or "")).strip()
    if not text:
        return "未知错误(无输出)"
    for pat, tag in _RULES:
        m = re.search(pat, text)
        if m:
            line = next((l.strip() for l in text.splitlines()
                         if re.search(pat, l)), m.group(0))
            return f"{tag}: {line.strip()[:limit - len(tag) - 2]}"
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not _NOISE.match(l)]
    tail = lines[-1] if lines else text.replace("\n", " ")[:limit]
    return tail[:limit]
