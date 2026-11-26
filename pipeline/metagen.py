"""标题/简介/标签生成：LLM 优先，模板兜底。"""
import logging
import re
from pathlib import Path

from . import config as C
from . import llm

log = logging.getLogger("metagen")

DISCLAIMER = "视频仅供学习交流，如有侵权请联系删除。"

LINE_STYLE = {
    "music": "B站音乐区搬运风格：可用【系列名/UP主】前缀，突出歌手、歌名、现场看点（一镜到底/无修音/万人合唱/4K画质等），可保留外文歌名",
    "relax": "B站解压区搬运风格：标题以【沉浸式XX】或品类词开头，突出过程爽点和反差（脏→干净/锈→镜面/乱→整齐），口语化有吸引力",
    "adventure": "B站户外探险区风格：突出悬念和收获感（意外发现/极端环境/巨大收获/硬核过程），可用【荒野XX】【寻宝】前缀，强调真实、原始、危机与反转，口语化勾人",
}

SYSTEM = """你是B站资深搬运号运营，负责把外网优质视频包装成B站投稿。
根据视频信息输出 JSON：{"title": "...", "desc": "...", "tags": ["..."]}
要求：
- title 全中文为主(歌名/人名可留原文)，含标点总长≤55字，B站风格，突出看点和情绪价值，不夸大不标题党
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
        line_style = LINE_STYLE.get(item.get("line", ""), LINE_STYLE["relax"])
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
