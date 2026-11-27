"""中文字幕：策略判断 + 翻译/转写 + 烧录。

策略优先级: 源级 subtitle 字段 → subtitles.policy[line] → auto
  always    强制配(没字幕也whisper转写)
  auto      有官方字幕→翻译；没有→whisper探测有语音才配
  subs_only 只翻译官方字幕(音乐线默认: 歌词转写不可靠)
  never     不配

语音探测: 抽取前3分钟音频 whisper 转写，词密度达标才算"有语音"。
"""
import json
import logging
import re
import subprocess
from pathlib import Path

from . import config as C
from . import llm

log = logging.getLogger("subtitle")

SRT_RE = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}[,.]\d+)\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d+)\s*\n(.*?)(?=\n\s*\n|\Z)",
    re.S)
_SPEECH_MIN_CPM = 60          # 前3分钟字符/分钟阈值(VAD后)，低于视为无解说
_CHECK_SECS = 180

_model_cache: dict = {}


def _parse_blocks(text: str) -> list[tuple[str, str, str]]:
    blocks = []
    for m in SRT_RE.finditer(text):
        body = re.sub(r"<[^>]+>", "", m.group(4)).strip()
        if body:
            blocks.append((m.group(2), m.group(3), body))
    return blocks


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text))


def _ts(sec: float) -> str:
    ms = int(sec * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _model(cfg: dict):
    import os
    os.environ.setdefault("HF_HOME", str(C.ROOT / "data" / "models" / "hf"))
    from faster_whisper import WhisperModel
    name = cfg.get("subtitles", {}).get("whisper_model", "small")
    if name not in _model_cache:
        dev = cfg.get("subtitles", {}).get("whisper_device", "cpu")
        try:
            _model_cache[name] = WhisperModel(name, device=dev, compute_type="int8")
        except Exception:
            # mps 等实验后端初始化失败时回退 cpu，不炸流水线
            _model_cache[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _model_cache[name]


def _load_audio(video_path: str, secs: int | None = None):
    """ffmpeg 解码成 16kHz 单声道 float32，绕过 PyAV 版本兼容问题。"""
    import numpy as np
    cmd = ["ffmpeg"]
    if secs:
        cmd += ["-t", str(secs)]
    cmd += ["-i", video_path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def speech_check(video_path: str, cfg: dict) -> dict:
    """前3分钟语音探测。VAD 挡幻觉，字符/分钟跨语种可比。"""
    audio = _load_audio(video_path, _CHECK_SECS)
    segments, _ = _model(cfg).transcribe(audio, beam_size=3, vad_filter=True)
    chars = sum(len(re.sub(r"\s", "", s.text)) for s in segments)
    cpm = chars / (_CHECK_SECS / 60)
    return {"speech": cpm >= _SPEECH_MIN_CPM, "cpm": round(cpm, 1)}


def _transcribe_full(video_path: str, cfg: dict, item_id: int = 0) -> str | None:
    """全片转写 → 原文 srt。"""
    from . import progress
    audio = _load_audio(video_path)
    total = len(audio) / 16000                       # 16kHz 单声道
    segments, _ = _model(cfg).transcribe(audio, beam_size=3, vad_filter=True)
    out = str(Path(video_path).with_name("video.orig.srt"))
    lines = []
    for i, s in enumerate(segments, 1):
        if s.text.strip():
            lines.append(f"{i}\n{_ts(s.start)} --> {_ts(s.end)}\n{s.text.strip()}\n")
        if item_id:
            progress.set_stage(item_id, "转写中", min(s.end / max(total, 1), 0.99),
                               f"{int(s.end)}s/{int(total)}s")
    if not lines:
        return None
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    return out


LINES_SCHEMA = {"type": "object",
                "properties": {"lines": {"type": "array", "items": {"type": "string"},
                                         "description": "译文数组，长度必须与输入一致"}},
                "required": ["lines"]}

_TRANS_SYSTEM = (
    "你是字幕翻译。把输入的外文字幕逐行译成自然中文(口语化、简洁、保留专有名词、不合并不拆分行)。"
    "输入JSON: {\"lines\":[原文...]}；输出同结构JSON: {\"lines\":[译文...]}，lines数量必须和输入完全一致。")


def _translate_lines(texts: list[str], cfg: dict, depth: int = 0) -> list[str]:
    """译一批字幕行；行数不符时重试一次，再不符二分递归，单行兜底原文。"""
    for _ in range(2):
        data = llm.gen_json(cfg, _TRANS_SYSTEM, json.dumps(
            {"lines": texts}, ensure_ascii=False), schema=LINES_SCHEMA)
        lines = (data or {}).get("lines") or []
        if len(lines) == len(texts):
            return lines
    if len(texts) == 1 or depth >= 4:
        return texts                                    # 兜底保留原文
    mid = len(texts) // 2
    return (_translate_lines(texts[:mid], cfg, depth + 1)
            + _translate_lines(texts[mid:], cfg, depth + 1))


def _translate_srt(src_path: str, dst_path: str, cfg: dict, item_id: int = 0) -> bool:
    from . import progress
    if src_path.endswith(".vtt"):           # vtt 时间戳格式不同，先转 srt
        conv = str(Path(src_path).with_suffix(".conv.srt"))
        subprocess.run(["ffmpeg", "-y", "-i", src_path, conv], capture_output=True)
        if Path(conv).exists():
            src_path = conv
    blocks = _parse_blocks(Path(src_path).read_text(encoding="utf-8", errors="ignore"))
    if not blocks:
        return False
    zh_lines: list[str] = []
    batch = 20
    for i in range(0, len(blocks), batch):
        zh_lines += _translate_lines([b[2] for b in blocks[i:i + batch]], cfg)
        if item_id:
            progress.set_stage(item_id, "翻译中",
                               min((i + batch) / len(blocks), 0.99),
                               f"{min(i + batch, len(blocks))}/{len(blocks)}块")
    out = [f"{i}\n{b[0]} --> {b[1]}\n{t}\n" for i, (b, t) in enumerate(zip(blocks, zh_lines), 1)]
    Path(dst_path).write_text("\n".join(out), encoding="utf-8")
    return True


def _video_size(video_path: str) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "quiet", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0",
                        video_path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        w, h = r.stdout.strip().split(",")
        return int(w), int(h)
    except ValueError:
        return 1920, 1080


_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    # Linux (fonts-noto-cjk / wqy)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]


def _font(size: int):
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _ts_sec(ts: str) -> float:
    h, m, s = ts.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _wrap_lines(text: str, max_w: int, font) -> list:
    """逐字符换行，中日英通吃。"""
    from PIL import Image, ImageDraw
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    lines = []
    for raw in text.split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        cur = ""
        for ch in raw:
            if probe.textlength(cur + ch, font=font) > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
    return lines


def _render_cue_png(text: str, video_w: int, font, font_size: int, stroke: int):
    """透明背景居中白字黑边 PNG，宽度0.92倍画面。超2行自动缩字号适配。"""
    from PIL import Image, ImageDraw
    max_w = int(video_w * 0.92)
    lines = _wrap_lines(text, max_w, font)
    if not lines:
        return None
    while len(lines) > 2 and font_size > 16:   # 长台词缩字号压回2行
        font_size = int(font_size * 0.82)
        font = _font(font_size)
        stroke = max(2, font_size // 9)
        lines = _wrap_lines(text, max_w, font)
    lh = int(font_size * 1.4)
    img = Image.new("RGBA", (max_w, lh * len(lines) + stroke * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = stroke * 2
    for ln in lines:
        x = (max_w - d.textlength(ln, font=font)) / 2
        d.text((x, y), ln, font=font, fill=(255, 255, 255, 255),
               stroke_width=stroke, stroke_fill=(0, 0, 0, 220))
        y += lh
    return img


def _video_duration(video_path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                        "-of", "csv=p=0", video_path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _run_ffmpeg_progress(cmd: list[str], total_sec: float, item_id: int,
                         base: float, span: float) -> subprocess.CompletedProcess:
    """跑 ffmpeg 并解析 -progress 输出上报烧录进度。"""
    from . import progress
    import re as _re
    cmd = cmd[:1] + ["-nostats", "-progress", "pipe:2"] + cmd[1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="ignore")
    tail = ""
    for line in proc.stderr:
        tail = (tail + line)[-4000:]
        m = _re.match(r"out_time=(\d+):(\d+):([\d.]+)", line.strip())
        if m and total_sec > 0 and item_id:
            cur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            progress.set_stage(item_id, "烧录中",
                               base + span * min(cur / total_sec, 0.99),
                               f"{int(cur)}s/{int(total_sec)}s")
    proc.wait()
    r = subprocess.CompletedProcess(cmd, proc.returncode, "", tail)
    r.stderr = tail
    return r


def burn_subtitle(video_path: str, srt_path: str, cfg: dict, item_id: int = 0) -> str | None:
    """中文字幕烧进画面：Pillow 渲染 PNG → ffmpeg overlay(本机ffmpeg无libass)。
    字号按分辨率自适应，位置/样式可配置。"""
    sc = cfg.get("subtitles", {})
    if srt_path.endswith(".vtt"):          # vtt 时间戳格式不同，先转 srt
        conv = str(Path(srt_path).with_suffix(".forburn.srt"))
        subprocess.run(["ffmpeg", "-y", "-i", srt_path, conv], capture_output=True)
        if Path(conv).exists():
            srt_path = conv
    blocks = _parse_blocks(Path(srt_path).read_text(encoding="utf-8", errors="ignore"))
    if not blocks:
        return None
    vw, vh = _video_size(video_path)
    # 字幕组惯例: 字高≈画面1/18, 距底≈7.5%高度, 粗描边; 1080p≈60px/margin≈81
    font_size = int(sc.get("font_size") or 0) or max(24, round(vh / 18))
    margin_v = int(sc.get("margin_v") or 0) or round(vh * 0.075)
    font = _font(font_size)
    stroke = max(2, font_size // 9)
    tmp = Path(video_path).parent / ".subimg"
    tmp.mkdir(exist_ok=True)
    out = str(Path(video_path).with_name("video_sub.mp4"))
    total_sec = _video_duration(video_path)
    # 软 fd 上限拉高后单批可放更多 overlay，减少重编码次数(每过一批=整片重编一次)
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft < 4096:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(4096, hard), hard))
    except Exception:
        pass
    chunks = [blocks[i:i + 300] for i in range(0, len(blocks), 300)]
    try:
        cur = video_path
        for pi, chunk in enumerate(chunks):
            inputs, graph, last = [], [], "0:v"
            for j, (st, en, text) in enumerate(chunk):
                img = _render_cue_png(text, vw, font, font_size, stroke)
                if img is None:
                    continue
                png = tmp / f"p{pi}_{j:04d}.png"
                img.save(png)
                inputs += ["-i", str(png)]
                out_l = f"v{j}"
                graph.append(
                    f"[{last}][{len(inputs)//2}:v]overlay=x=(W-w)/2:y=H-h-{margin_v}:"
                    f"enable='between(t,{_ts_sec(st)},{_ts_sec(en)})'[{out_l}]")
                last = out_l
            if not inputs:
                continue
            dst = out if pi == len(chunks) - 1 else str(tmp / f"pass{pi}.mp4")
            r = _run_ffmpeg_progress(
                ["ffmpeg", "-y", "-i", cur, *inputs,
                 "-filter_complex", ";\n".join(graph),
                 "-map", f"[{last}]", "-map", "0:a?",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "copy", dst],
                total_sec, item_id, pi / len(chunks), 1 / len(chunks))
            if r.returncode != 0:
                log.warning("burn pass%d ffmpeg rc=%s: %s", pi, r.returncode, r.stderr[-300:])
                return None
            cur = dst
        return out if Path(out).exists() else None
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _policy(item: dict, cfg: dict) -> str:
    src = C.get_source(cfg, item.get("source_name") or "") or {}
    if src.get("subtitle"):
        return src["subtitle"]
    line_pol = (cfg.get("subtitles", {}).get("policy") or {}).get(item.get("line", ""))
    return line_pol or "auto"


def ensure_zh_subtitle(item: dict, cfg: dict) -> dict:
    """{subtitle_path, file_path?}。enabled=false 或不配字幕策略时返回 {}。"""
    sc = cfg.get("subtitles", {})
    if not sc.get("enabled"):
        return {}
    video = item.get("file_path")
    if not video:
        return {}
    policy = _policy(item, cfg)
    if policy == "never":
        return {}

    from . import progress
    iid = item.get("id") or 0
    out = {}
    existing = item.get("subtitle_path") or ""
    zh_path = str(Path(video).with_name("video.zh.srt"))

    if existing and _has_cjk(Path(existing).read_text(encoding="utf-8", errors="ignore")[:5000]):
        out["subtitle_path"] = existing                     # 已是中文字幕
    elif existing:                                          # 有官方外语字幕→翻译
        try:
            progress.set_stage(iid, "翻译官方字幕中")
            if _translate_srt(existing, zh_path, cfg, iid):
                out["subtitle_path"] = zh_path
        except Exception as e:
            log.warning("translate failed: %s", e)
    elif policy == "always":                                # 无条件转写
        try:
            progress.set_stage(iid, "转写中")
            raw = _transcribe_full(video, cfg, iid)
            if raw:
                progress.set_stage(iid, "翻译中")
            if raw and _translate_srt(raw, zh_path, cfg, iid):
                out["subtitle_path"] = zh_path
        except Exception as e:
            log.warning("transcribe failed: %s", e)
    elif policy == "auto":                                  # 探测有语音才转写
        try:
            progress.set_stage(iid, "语音探测中")
            chk = speech_check(video, cfg)
            log.info("item %s speech_check: %s", item.get("id"), chk)
            if chk["speech"]:
                progress.set_stage(iid, "转写中")
                raw = _transcribe_full(video, cfg, iid)
                if raw:
                    progress.set_stage(iid, "翻译中")
                if raw and _translate_srt(raw, zh_path, cfg, iid):
                    out["subtitle_path"] = zh_path
        except Exception as e:
            log.warning("speech check failed: %s", e)
    # subs_only: 没有官方字幕就直接跳过

    # 已烧过(video_sub.mp4)不重复烧——否则输入=输出会毁文件
    if (sc.get("burn", True) and out.get("subtitle_path")
            and Path(video).stem != "video_sub"):
        progress.set_stage(iid, "烧录中", 0)
        burned = burn_subtitle(video, out["subtitle_path"], cfg, iid)
        if burned:
            out["file_path"] = burned
            try:  # 原件删掉省一倍空间(video_sub 就是发布文件, 重跑也不会重复烧)
                Path(video).unlink()
            except OSError:
                pass
    return out
