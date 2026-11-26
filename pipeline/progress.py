"""跨线程进度上报中心。

下载/语音探测/转写/翻译/烧录/上传各环节向这里写实时状态，
web 层 /api/progress 读快照渲染进度条。进程内字典，重启即空(任务本就重启即丢)。
"""
import threading
import time

_LOCK = threading.Lock()
_ITEMS: dict[int, dict] = {}


def set_stage(item_id: int, stage: str, pct: float | None = None, detail: str = ""):
    """pct: 0~1 或 None(不确定进度只显示阶段)。"""
    with _LOCK:
        _ITEMS[item_id] = {"stage": stage, "pct": pct,
                           "detail": detail, "ts": time.time()}


def clear(item_id: int):
    with _LOCK:
        _ITEMS.pop(item_id, None)


def snapshot() -> dict:
    with _LOCK:
        return {k: dict(v) for k, v in _ITEMS.items()}
