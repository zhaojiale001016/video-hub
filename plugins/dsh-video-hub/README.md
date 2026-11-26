# dsh-video-hub

video-hub 多平台视频搬运中台的 **DeepSeek Harness 插件** — 把管理台全部能力注册成 20 个 `hub_*` LLM 工具，在 dsh 会话里直接让 agent 查队列、改标题、发布、补投、加源。

## 前置

- video-hub 管理服务在跑：`cli.py serve`（或 launchd/systemd 常驻）→ `http://127.0.0.1:8600`
- DeepSeek Harness ≥ 0.2.0-rc.2 / 0.2.1-rc.2

## 安装（三种，任选）

**① 插件市场（推荐，DSH 内点一下就装完）**

```
Plugins → 实验插件开「Custom plugin indexes」→ Discover → Add index source:
  https://zhaojiale001016.github.io/video-hub/dsh-index.json
→ 搜 video-hub → Install → 确认(commit/目录都给你看) → 重启
```

**② 命令行（GitHub 源码安装）**

```bash
dsh plugin --profile <profile> add \
  'zhaojiale001016/video-hub#path:/plugins/dsh-video-hub'
# 固定版本: 在 #path: 前再加一段 #<tag>, 如
# 'zhaojiale001016/video-hub#v1.6.0#path:/plugins/dsh-video-hub'
```

**③ 本地目录（开发调试）**

```bash
dsh plugin --profile <profile> add /path/to/video-hub/plugins/dsh-video-hub
```

验证层生效：`dsh --profile desktop --dump-config` 应出现 `# == dsh-video-hub` 层。
卸载：`dsh plugin --profile desktop remove dsh-video-hub`

## 配置

patch 行默认 `api: http://127.0.0.1:8600`；改端口就在 profile 的 `cordis.patch.yml`
覆盖该行 config，或设环境变量 `VIDEOHUB_API`。

## 工具速查

队列：`hub_stats` `hub_list` `hub_get` `hub_progress`
动作：`hub_publish` `hub_publish_all` `hub_poll` `hub_download` `hub_import` `hub_skip` `hub_retry` `hub_regen` `hub_delete_files` `hub_edit`
平台/源：`hub_platforms` `hub_sources` `hub_add_source`
任务：`hub_jobs` `hub_job` `hub_wait_job`（长任务全部返回 job_id，用 wait_job 收尾）

## 语义红线（agent 友好性）

- 已 `published` 的稿件不可改元数据；直接 `hub_publish` 会**只补投失败平台**（已成功平台自动去重）
- 全部勾选平台成功才自动删文件；部分成功保留文件=补投材料，别轻易 `hub_delete_files`
- `copyright`: 1=自制 2=转载（转载必须 source_url，禁参加 B站活动）
