# Changelog

## v1.7.4 (2025-06)
### Fixed
- `submit_api`/`tencent_content_label` 补进 SETTINGS_SCHEMA——之前设置面板 PUT 会静默丢弃这两个键
- 设置面板新增「📤 投稿」分组展示两键（B站接口 app|web 下拉 + 视频号标注文案）

## v1.7.3 (2025-06)
### Added
- **双封面体系**：每条稿件生成 16:9 横版（`video.jpg`，B站用）+ 中心裁剪 3:4 竖版（`cover_portrait.jpg`，ffmpeg 双向 min() 恒出 3:4）；新列 `cover_portrait_path` 自动迁移
- tencent/douyin 双封面位全喂（`--thumbnail-landscape`+`--thumbnail-portrait`）；kuaishou/xiaohongshu 竖版信息流优先喂 3:4

### Fixed
- vendored tencent uploader 封面对话框：`count>0` 即时检查→`wait_for(visible,8s)`（Vue 挂载慢会漏），上传后等待 5s→15s——之前「编辑封面」超时被静默跳过，稿件裸发无封面

## v1.7.2 (2025-06)
### Fixed
- **视频号不再自动勾「含AI生成内容」标注**：vendored sau 上游硬编码该标注（其内容是AI生成）；`apply_original_statement` 补丁支持 `SAU_TENCENT_CONTENT_LABEL` 环境变量，`none/off/skip` 整段跳过；multipub 由新设置项 `tencent_content_label`（默认 `none`）注入——已发出去的两条在视频号助手后台可手动改标
- 已标记标注的稿件不受影响（标注是发布时动作，不可追溯改）

## v1.7.1 (2025-06)
### Changed
- **B站投稿默认走 `app` 接口**：B站已废弃客户端(web)提交接口，biliup 每次都先吃 WARN 再降级；新增 `submit_api` 设置项（默认 `app`，可改回 `web`）——实测 `app` 接口直接成功
- 前端错误 toast 解析 FastAPI `detail` 字段，不再显示裸 JSON

## v1.7.0 (2025-06)
### Added
- **本地文件导入发布**：`downloader.import_local_file()` 把本机视频复制进 `download_dir/<id>/`（`video.<ext>` 归一化，可选封面同步导入 `.webp→.jpg`），`(platform=local, source_id=sha256)` 去重防重复导入；不占每日下载额度
- `POST /api/import`：异步入库（`process=true` 直接进字幕+元数据流水线→ready 返回 job_id；`false` 只入库停 `new`）
- `cli.py import <path>`：本地导入命令，`--line/--title/--no-process` 参数
- 管理台顶栏「📁 导入本地」对话框（路径/内容线/原标题/是否立即处理）
- `hub_import` 第 20 个 agent 工具（MCP + DSH 插件同步）
- `cli.py release-plugin`：DSH 插件发版命令——bump `plugins/dsh-video-hub/package.json` version + 重写 `docs/dsh-index.json` versions 条目（commit 指向当前 HEAD）

### Fixed
- `cli.py import` 文件不存在时输出可读错误而非 traceback

## v1.6.0 (2025-06)
### Added
- **DeepSeek Harness 原生插件**：`plugins/dsh-video-hub/` 标准 dsh bundle 包（`dsh.bundle.patch` + `cordis.patch.yml` + 纯 JS host entry），把 19 个 `hub_*` 工具注册进 dsh 原生 `tools` 服务（非外挂 MCP）；`dsh plugin --profile <p> add` 一条命令安装，config.api / `VIDEOHUB_API` 可换 API 地址
- **插件市场索引**：`docs/dsh-index.json` + `docs/icons/` 随 GitHub Pages 发布——用户在 DSH 开「Custom plugin indexes」加源即可 Discover 一键安装（git 源指向 tag commit+子目录）；仓库加 `dsh-plugin`/`deepseek-harness` topic 进社区目录自动收录
- `tests/test_dsh_plugin.py` 5 例：manifest/patch 结构、19 工具齐全、node 沙箱注册+离线错误兜底

## v1.5.0 (2025-06)
### Added
- **AI Agent 接入**：`mcp_server.py` 零依赖 stdio MCP server（19 个 `hub_*` 工具），`cli.py mcp` 启动；`skills/video-hub/SKILL.md` 通用 skill；`AGENTS.md` 仓库导引
- README 新章「给 AI Agent 用」（MCP/Skill/裸 API 三选一）

## v1.4.0 (2025-06)
### Added
- **测试体系**：49 个 pytest 用例（uploader/multipub/db/precheck/config），CI 新增 pytest job
- 跨平台：字幕字体补 Linux/Windows 候选、`_notify` 分流 notify-send、`_sau_py` 兼容 Windows、`deploy/video-hub.service.example` systemd 单元
- `serve()` 非本机绑定拒绝启动（`VIDEOHUB_ALLOW_REMOTE=1` 显式放开）；`valid_platform_id()` 统一校验收口
- `vendor/sau/PATCHES.md` 补丁清单、本 CHANGELOG

### Fixed
- precheck 相似度加 bigram Jaccard（抗语序重排）；uploader 标题查重复用归一化
- `_next_slot_ts` 计入 failed 条目 dtime；`config` 空节（`settings: null`）不再 AttributeError

## v1.3.7 (2025-06)
### Fixed
- 启动调和误删文件：`delete_after_publish` 开启时，部分平台未投完的稿件文件在重启时被删，补投材料全毁。现仅 `all_selected_done` 才自动清理
- 过期/临期 `dtime` 仍传 `--dtime` → B站 API 拒绝，定时稿失败后永远无法重试成功。已加 `>now+600s` 守卫
- `extra_platforms` 键无校验：`../x` 可注入 cookie 文件名、`bilibili` 可致重复投递 → PUT 强制 slug+拒保留字，`account` 同
- `_delete_files` `rmtree(parent)` 无护栏 → 仅整删 `download_dir/<id>/` 约定布局

### Docs
- README/官网 quickstart 补 sau 引擎安装（venv + `pip -e .` + `patchright install chromium`）

## v1.3.6 (2025-06)
- PATCH `pub_platforms` 白名单放开为 slug 校验（此前勾选新平台会被静默丢弃）
- 无显式勾选的条目保存后不再固化成显式列表（保留"跟随启用集"语义）
- `doctor` 动态输出全部已启用平台登录态；`fmtPoll` 整点显示修复
- 抽屉分组：通用字段 → 平台勾选 → ⓑ B站专属（分区/版权/活动/定时）

## v1.3.5 (2025-06)
- sau 能力矩阵：`_NO_THUMB`/`_NO_TAGS`/`_NO_SCHEDULE`/`_LAND_THUMB` 按平台适配参数（知乎无 tags、微博等无定时）
- 剔除纯图文平台 facebook/xueqiu（无 upload-video）；保留字拦截 bilibili/article
- `stale_days=0` 修复为"关闭积压清理"（此前会瞬间判全部过期）
- 抽屉 `sel` 作用域 bug 修复、`/api/platforms` 懒加载保底

## v1.3.4 (2025-06)
- 状态栏重排：登录态合并卡（彩点）+ 配额迷你进度条 + `5h55m` 人性化轮询显示

## v1.3.3 (2025-06)
- 设置面板重设计：toggle 开关、number 输入框、字段 hint 带后端默认值、卡片化分组、15 平台三列网格

## v1.3.2 (2025-06)
- 「启用多平台」改全量勾选列表：13 个 sau 视频平台中文名+id 直出，不用记 id

## v1.3.1 (2025-06)
- 前端观感与后端对齐：副标题/登录卡/补投判定全部走 `/api/platforms` 动态数据

## v1.3.0 (2025-06)
- 平台注册开放：`extra_platforms` 通配 schema、`/api/platforms` 端点、`cli.py login-extra <id>` 通用登录
- 阈值参数化：`precheck_threshold`/`precheck_autoskip`/`stale_days`/`publish_slot_gap_h` 进设置面板

## v1.2.0 (2025-06)
- 内容线完全自定义：`line` 任意小写 slug，徽标哈希配色，字幕策略/活动匹配/LLM 风格按同名 key 联动

## v1.1.0 (2025-05)
- 逐平台投递留痕：`extra_pub` 记录各平台成败，已发平台不重复投
- 抽屉「↻ 补投失败平台」「🗑 清理文件」入口；`DELETE /api/items/{id}/files`
- 删除规则改为全平台成功才删文件（部分成功保留补投材料）

## v1.0.1 (2025-05)
- B站域失败（日限额/撞稿临界/biliup报错）不再阻断快手/视频号等其余勾选平台

## v1.0.0 (2025-05)
- 开源封板：五步流水线（轮询→下载+字幕→LLM元数据→审核→多平台投递）、五层防重、管理台、launchd 常驻
