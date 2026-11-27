"""LLM 调用层：支持 kscc CLI(本地) / OpenAI兼容API 两种后端。

kscc 后端: `kscc -p --bare --model fb --output-format json --json-schema <s>` 
api 后端: OpenAI /chat/completions (DeepSeek/Kimi/通义等)
"""
import json
import logging
import os
import shutil
import subprocess

import httpx

log = logging.getLogger("llm")

META_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "B站投稿标题,全中文为主,含标点≤55字"},
        "desc": {"type": "string", "description": "B站投稿简介,80~200字"},
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "5~8个标签,优先中文"},
    },
    "required": ["title", "desc", "tags"],
}


def llm_cfg(cfg: dict) -> dict:
    return cfg.get("llm", {})


def backend(cfg: dict) -> str:
    return llm_cfg(cfg).get("backend", "kscc")


def is_enabled(cfg: dict) -> bool:
    if not llm_cfg(cfg).get("enabled", True):
        return False
    if backend(cfg) == "kscc":
        return shutil.which("kscc") is not None
    return bool(os.environ.get(llm_cfg(cfg).get("api_key_env", "LLM_API_KEY")))


def gen_json(cfg: dict, system: str, user: str,
             schema: dict | None = None) -> dict | None:
    """生成 JSON 结果，失败返回 None。schema 可自定义(默认标题schema)。"""
    if backend(cfg) == "kscc":
        return _kscc_gen_json(cfg, system, user, schema)
    return _api_gen_json(cfg, system, user)


# ---------------- kscc CLI 后端 ----------------

def _kscc_gen_json(cfg: dict, system: str, user: str,
                   schema: dict | None = None) -> dict | None:
    model = llm_cfg(cfg).get("model", "fb")
    timeout = int(llm_cfg(cfg).get("timeout", 120))
    prompt = f"{system}\n\n{user}"
    cmd = ["kscc", "-p", "--bare", "--model", model,
           "--output-format", "json",
           "--json-schema", json.dumps(schema or META_SCHEMA)]
    try:
        proc = subprocess.run(cmd + [prompt], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if proc.returncode != 0:
            log.warning("kscc failed: %s", (proc.stderr or proc.stdout)[-300:])
            return None
        out = json.loads(proc.stdout)
        if out.get("is_error"):
            log.warning("kscc error: %s", str(out.get("result"))[:300])
            return None
        data = out.get("structured_output")
        if isinstance(data, dict) and data:
            return data
        # 兜底: result 是 JSON 字符串
        res = out.get("result") or ""
        s, e = res.find("{"), res.rfind("}")
        if s != -1 and e != -1:
            return json.loads(res[s:e + 1])
    except Exception as e:
        log.warning("kscc gen_json failed: %s", e)
    return None


# ---------------- OpenAI 兼容 API 后端 ----------------

def _api_gen_json(cfg: dict, system: str, user: str) -> dict | None:
    lc = llm_cfg(cfg)
    key = os.environ.get(lc.get("api_key_env", "LLM_API_KEY"), "")
    if not key:
        return None
    base = lc.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
    try:
        r = httpx.post(f"{base}/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json={
                           "model": lc.get("model", "deepseek-chat"),
                           "messages": [
                               {"role": "system", "content": system},
                               {"role": "user", "content": user}],
                           "temperature": 0.7,
                           "response_format": {"type": "json_object"},
                       }, timeout=lc.get("timeout", 45))
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1:
            return json.loads(text[s:e + 1])
    except Exception as e:
        log.warning("api gen_json failed: %s", e)
    return None
