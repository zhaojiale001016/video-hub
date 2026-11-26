# AGENTS.md — 给 coding agent 的仓库导引

视频搬运中台: 订阅源→yt-dlp下载→whisper字幕→LLM元数据→审核→多平台投递。
本地私有, 无鉴权(仅 127.0.0.1), 数据全在 `data/`(已 gitignore)。

## 结构速查

| 路径 | 作用 |
|---|---|
| `cli.py` | 全部手动入口(poll/download/publish/login/serve/doctor) |
| `pipeline/` | 12 个模块: config(覆盖层) db(sqlite) downloader poller precheck(撞稿) subtitle(whisper+烧录) metagen(LLM) missions(B站活动) uploader(B站投递) multipub(sau多平台) progress source_health |
| `web/app.py` | FastAPI 管理台, 全部 API + 后台任务 + 启动调和 |
| `web/static/index.html` | 单文件前端(vanilla JS) |
| `mcp_server.py` | MCP stdio server(零协议依赖, 薄封装 HTTP API) |
| `vendor/sau/` | social-auto-upload vendored 副本(见 PATCHES.md 本地补丁清单) |
| `config/` | sources.yaml(私有)+example+overrides.json(私有,UI写入) |

## 关键语义(改代码前必读)

- **多平台独立**: `extra_pub` 记每平台成败; 已成功平台重试时跳过; 全平台成功才自动删文件
- **保留平台id**: `bilibili`(内置 biliup 通道)/`article`; extra_platforms 键必须 slug
- **sau 能力矩阵**: `_NO_THUMB/_NO_TAGS/_NO_SCHEDULE/_LAND_THUMB` —— 加平台先查 sau_commands/*.py 的 argparse
- **内容线自由**: `line` 任意小写 slug, 字幕策略/missions.match/llm.line_styles 同名 key 联动
- **B站域失败不阻断**: 任何B站环节失败只记 extra_pub.bilibili, 其余平台照常
- **published 不可 PATCH**(与线上稿件脱节), publish() 对 published 放行是为补投(去重保护)

## 环境命令

```bash
.venv/bin/python -m pytest tests/ -q          # 单测(49例)
.venv/bin/python -m compileall pipeline web cli.py mcp_server.py
.venv/bin/python3 cli.py doctor                # 体检(登录态/队列/额度)
curl -s http://127.0.0.1:8600/api/stats        # 服务在跑时
```

## 提交约定

- 测试先行: 改 `pipeline/` 逻辑先在 tests/ 补用例
- 改 `vendor/sau/` 必须同步登记 `vendor/sau/PATCHES.md`
- 涉及新平台: 改 `multipub.SAU_PLATFORMS/PLATFORMS` + 能力矩阵 + 跑 `test_multipub`
- 配置项进 UI: `SETTINGS_SCHEMA` + SGROUPS(前端) + example.yaml 注释三处同步
