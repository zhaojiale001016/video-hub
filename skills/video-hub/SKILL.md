---
name: video-hub
description: 驱动本地 video-hub 视频搬运中台(订阅源→下载→字幕→LLM标题→审核→多平台投递)。当用户要查看队列/发布稿件/编辑标题/加订阅源/查任务进度/补投失败平台时触发。
---

# video-hub 操作指南

本地视频搬运中台。**优先级**: 若已配置 video-hub MCP server → 用 hub_* 工具;
否则用 REST API(http://127.0.0.1:8600, 可用 `curl`/httpx);
兜底用 CLI(`<项目>/.venv/bin/python3 cli.py <cmd>`, 项目根目录问用户或找 `config/sources.yaml`)。

## 前置检查

服务未响应时: `cli.py serve` 启动(或 `launchctl kickstart -k gui/$(id -u)/com.videohub.pipeline`)。
全量接口文档: `GET /openapi.json` 或浏览器 `/docs`。

## 状态机(别乱动)

`new → downloaded → ready → published` 旁路 `failed / skipped`。

| 你想做 | 正确做法 |
|---|---|
| 看队列 | `hub_stats` / `hub_list(status)` |
| 发一条 | `hub_publish(id)` → 拿 job_id → `hub_wait_job(job_id)` |
| 发全部 | `hub_publish_all(schedule=true)` 错峰定时 |
| 改标题/简介/标签/定时 | `hub_edit(id, ...)` —— **published 不可改** |
| 补投失败平台 | published 稿件直接 `hub_publish(id)`, 已成功平台自动跳过 |
| 失败重来 | `hub_retry(id)`(文件在→ready, 不在→重新下载) |
| 不要了 | `hub_skip(id)`(会删文件) / `hub_skip(id, undo=true)` 撤回 |
| 加源 | `hub_add_source(name,url,line)`, line 任意小写 slug 自定 |

## 关键语义(容易踩)

- **多平台独立**: 每个平台结果记 `extra_pub[platform].ok`; 已成功的平台重试时**绝不重投**
- **删文件规则**: 全部勾选平台成功才自动删; 部分成功文件保留(是补投材料, 别手动删)
- **B站专属字段**: `tid`分区/`mission_id`活动/`copyright`版权/dtime定时——只影响B站通道;
  sau 平台(快手/抖音等)能力差异自动适配(有的没封面/定时)
- **copyright**: 1=自制 2=转载(转载必须 source_url, 禁参加活动)
- **长任务全异步**: poll/download/publish 返回 job_id, 用 `hub_job`/`hub_wait_job` 收尾,
  别以为调用返回=做完了

## 排障

`hub_platforms` 看启用态; `hub_progress` 看在飞进度; `cli.py doctor` 体检登录态。
平台登录失效: `cli.py login-extra <平台id>` 扫码(弹浏览器, 引导用户操作)。
