# vendored social-auto-upload 本地补丁

本目录是 [social-auto-upload](https://github.com/dreammis/social-auto-upload) 的 vendored 副本。
直接 `git pull` 上游会覆盖下列本地改动，升级后需重新打补丁。

## 补丁清单

| 文件 | 改动 | 原因 |
|---|---|---|
| `uploader/ks_uploader/main.py` | `KUAISHOU_UPLOAD_TIMEOUT_SECONDS` 480 → **1500** | 原值对 >400MB 大文件上传不够用，提到 25 分钟 |
| `uploader/tencent_uploader/main.py` | `apply_original_statement` 支持 `SAU_TENCENT_CONTENT_LABEL` 环境变量覆盖标注文案；`none/off/skip` 时整段跳过 | 上游硬编码「含AI生成内容」，搬运内容不该勾；multipub 由设置项 `tencent_content_label` 注入 |

## 本地集成约定（非补丁，是依赖关系）

`pipeline/multipub.py` 通过子进程调用本目录：

- 入口：`sau_cli.py <platform> upload-video|login ...`
- Python：`.venv/bin/python`（Windows 为 `.venv/Scripts/python.exe`），**独立 venv**，与主项目隔离
- cookie：`cookies/<platform>_<account>.json`（`logged_in()` 按此 glob）
- 能力矩阵：`multipub` 里 `_NO_THUMB`/`_NO_TAGS`/`_NO_SCHEDULE`/`_LAND_THUMB` 记录各平台参数差异，**升级上游后需对照 `sau_commands/*.py` 的 argparse 定义复核**

## 升级步骤

```bash
# 1. 备份本目录 → 覆盖上游新版
# 2. 重打上表补丁
# 3. cd vendor/sau && .venv/bin/pip install -e . && .venv/bin/patchright install chromium
# 4. 跑测试: python -m pytest tests/test_multipub.py  (mock 校验各平台命令合法)
# 5. 每个已启用平台跑一遍 login-extra + 单条试投
```
