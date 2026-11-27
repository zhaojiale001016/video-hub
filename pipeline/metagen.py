"""标题/简介/标签生成：LLM 优先，模板兜底。"""
import logging
import re
from pathlib import Path

from . import config as C
from . import llm

log = logging.getLogger("metagen")

DISCLAIMER = "视频仅供学习交流，如有侵权请联系删除。"

LINE_STYLE = {
    # 公共仓库不内置线名风格——个人内容线风格放私有 config llm.line_styles;
    # 未命中的线一律走下方 DEFAULT_LINE_STYLE
}
# 未配置 line_styles 的自定义内容线使用的通用风格
DEFAULT_LINE_STYLE = "短视频搬运通用风格：突出视频核心看点和反差爽点，口语化有吸引力，标题带【品类/看点】前缀"

SYSTEM = """你是短视频多平台搬运号运营，负责把外网优质视频包装成中文投稿(B站/抖音/快手/视频号/小红书等平台通用)。
根据视频信息输出 JSON：{"title": "...", "desc": "...", "tags": ["..."]}
要求：
- title 全中文为主(歌名/人名可留原文)，含标点总长≤55字，突出看点和情绪价值，不夸大不标题党
- desc 80~200字：一句话内容概括 + 2~3个看点 + 结尾固定加一句免责声明，不提来源链接/转载/原作者
- tags 5~8个：优先中文，必含原作者/频道名，每个≤12字
- 只输出 JSON，不要解释"""


def _subtitle_snippet(item: dict, limit: int = 500) -> str:
    p = item.get("subtitle_path")
    if not p:
        return ""
    try:
        text = Path(p).read_text(encoding="utf-8", errors="ignore")
        text = re.sub(r"<[^>]+>|\{\{[^}]+\}\}|^\d+$|^\s*$|.*?-->.*?\n", "", text, flags=re.M)
        return re.sub(r"\s+", " ", text).strip()[:limit]
    except OSError:
        return ""


def _clip_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip().strip('"')
    return t[:80]


def _clip_tags(tags: list, src_tags: list) -> list:
    out, seen = [], set()
    for t in list(tags or []) + list(src_tags or []):
        t = str(t).strip().lstrip("#")
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t[:20])
        if len(out) >= 12:
            break
    return out


def gen_meta(item: dict, cfg: dict, db=None) -> dict:
    """生成 {title, desc, tags, tid}。LLM 失败自动退回模板。"""
    src = C.get_source(cfg, item.get("source_name", "")) or {}
    tid = src.get("tid") or item.get("tid") or 21
    src_tags = src.get("tags") or []
    category = src.get("category") or ""

    if llm.is_enabled(cfg):
        # 内容线风格: 内置三线 + cfg.llm.line_styles 自定义覆盖 + 中性兜底
        line_styles = {**LINE_STYLE,
                       **((cfg.get("llm") or {}).get("line_styles") or {})}
        line_style = line_styles.get(item.get("line", ""), DEFAULT_LINE_STYLE)
        user = (
            f"业务方向: {line_style}\n"
            f"视频品类: {category}\n"
            f"原作者/频道: {item.get('uploader','')}\n"
            f"原标题: {item.get('orig_title','')}\n"
            f"原简介: {(item.get('orig_desc') or '')[:400]}\n"
            f"字幕片段: {_subtitle_snippet(item)}\n"
            f"建议标签: {', '.join(src_tags)}\n"
            f"免责声明请用: {DISCLAIMER}"
        )
        data = llm.gen_json(cfg, SYSTEM, user)
        if data and data.get("title"):
            return {"title": _clip_title(data["title"]),
                    "desc": (data.get("desc") or ""),
                    "tags": _clip_tags(data.get("tags"), src_tags),
                    "tid": tid}
        log.warning("item %s llm failed, fallback to template", item.get("id"))

    return _template_meta(item, src, tid, src_tags, category)


def _template_meta(item: dict, src: dict, tid: int, src_tags: list, category: str) -> dict:
    title = item.get("orig_title") or "未命名"
    uploader = item.get("uploader") or ""
    prefix = f"【{category or uploader}】" if (category or uploader) else ""
    full = _clip_title(prefix + title)
    desc = f"{title}\n\n{DISCLAIMER}"
    return {"title": full, "desc": desc, "tags": _clip_tags([], src_tags), "tid": tid}
