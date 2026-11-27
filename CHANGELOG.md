# Changelog

## v2.1.3 (2025-11) — 整体审计过审版

前后端四层审计全绿：API 调用面/路由对账零悬空、10 端点实况全 200、服务日志零错误、JS 语法+内联 handler 全对上。无代码变更，标记当前完整一致状态。

## v2.1.1 → v2.1.2 (2025-11) — 前端拆分回归修复

- **fix**: index.html 拆分残留 `<style>`/`<script>` 孤儿开标签吞掉后续 HTML → 页面空白/JS 不执行，已删
- **tests**: 页面外壳完整性 + 静态资源可达性回归断言(110例)
- **docs**: SKILL.md 工具表补平台登录/直达/验证码行

## v2.1.1 (2025-11) — Agent 工具补齐 + 前端拆分

- **hub_* 工具 20→23**：新增 `hub_platform_login`(拉起 sau 扫码登录) / `hub_platform_open`(登录态直达创作中心) / `hub_verify_code`(投喂验证码)——三个端点此前只通裸 API/前端，mcp+dsh 双端同步，dsh 插件发 0.3.0
- **前端拆分**：index.html(标记)/app.js/style.css 三件套——单文件技术债清偿
- **文档**：全站工具计数同步、官网 docker 部署行+错误归类、目录表对齐、AGENTS 技术债→设计约束

## v2.1.0 (2025-11) — 工程化补强

- **Docker 化**：Dockerfile(ffmpeg+CJK字体+双venv+sau chromium) / docker-compose.yml / .dockerignore；数据全卷挂载、端口绑回环、`cli.py serve --host`
- **集成测试层**：`test_integration.py` FastAPI TestClient 端点冒烟 + 状态机流转/保护分支 + extra_pub 逐腿记账（此前零 API 级测试）；`test_errfmt.py` 6 例 → 108/108
- **错误归类**：新增 `pipeline/errfmt.py`，子进程/第三方报错按模式归类（登录失效/风控/超时/磁盘/限流/审核），UI 不再吃原始 stderr；接入 multipub/uploader/downloader 三处
- **文档**：AGENTS.md 技术债清单、README Docker 部署小节

## v2.0.0 (2025-06) — 终版

完整功能集封板：订阅源→下载→字幕翻译烧录→LLM 中文元数据→审核→14 平台逐路投递，本地私有、五层防重、逐平台记账、agent 四通道接管。

终版能力全景：
- **平台中枢独立页**：14 平台登录态/投递启用/发稿统计/创作中心直达/扫码登录一页收齐
- **多平台独立投递**：extra_pub 逐平台记账、失败不阻断、补投跳过成功平台、全成才删文件
- **下载处理链**：片头黑段修剪/智能封面/3:4 竖封/字幕翻译烧录/LLM 元数据
- **防重体系**：DB 唯一键 + 本地导入内容指纹 + yt-dlp archive + published 免入 + 撞稿预检 + 同名拦截
- **调度**：墙钟到点制(睡眠唤醒补轮) + 工作日限定 + 内容线混排 + 分平台错峰
- **风控应对**：验证码 Web 通道 / 按平台有头模式 / 自主声明配置 / 登录态直达
- **开源卫生**：私有配置全 gitignored、公共仓库零私有线名

## v1.8.11 (2025-06)
### Fixed
- **隐私清污**：公共仓库中泄入的真实私有内容线名(music/relax/adventure)清除——metagen `LINE_STYLE` 内置键删空(走 DEFAULT_LINE_STYLE)、`.b-line-*` 死CSS删除、example/README/测试示例线名全量中性化(vlog/food/tech)

## v1.8.10 (2025-06)
### Docs
- README 全面对齐：平台中枢页、分平台进阶配置(account/offset_min/headed/declaration)、验证码Web通道、封面与片头处理、墙钟到点调度、download_mix_lines、本地导入内容指纹判重、流程图投递模型修正
- CHANGELOG 补 v1.8.5~v1.8.9 五版条目；AGENTS.md 模块清单修正

## v1.8.9 (2025-06)
### Fixed
- **本地导入重复导入→重复发布**：`source_id` 从 路径|大小|mtime 改为内容指纹（大小+头中尾各2MB采样哈希）——同视频换路径/改名/mtime变动/多副本原全部穿透去重，现按内容判重（已入库旧条目同文件最后一次穿透，之后永久判重）
- **创作中心地址全量勘误**：B站 home `york/home`(404)→`platform/home`、manage→`platform/upload-manager/video`；快手 manage→sau 常量 `article/manage/video?status=2&from=publish`；小红书 manage→`new/note-manager`、publish 补全真实投稿参数
- 创作中心直达不再拉起独立 Chromium（割裂感），全部改为 `target=_blank` 当前浏览器新标签

### Added
- **平台中枢升级独立管理页**（v1.8.8 面板演进）：弹窗废除改整页视图——全平台登录态/投递启用开关(直写 extra_platforms)/发稿成败数/创作中心·作品管理·发布页直达 + 扫码登录(B站QR / sau 平台 `login_cmd` 拉起) 一页收齐
- `POST /api/platforms/{pid}/login`（pid 校验防 glob 注入）与 `POST /api/platforms/{pid}/open`（sau storage_state 拉起带登录态浏览器；B站 biliup→playwright 转换；关窗回写 cookie 续命）
- 导航栏删除单独「🔐 B站登录」按钮，全平台登录收口平台页

## v1.8.8 (2025-06)
### Added
- **平台中枢面板**：全部 14 个可投平台的启用态/登录态/发稿成败数/创作中心·作品管理·发布页直达一屏收齐（`PLATFORM_META` + `db.platform_pub_counts` + `/api/platforms` 富字段）

### Fixed
- **`/api/platforms` 端点撞名**：新中枢端点与老端点同路径返回数组形状，抽屉投递勾选框/设置面板/顶栏角标全部渲染失败——合并为字典形状+富字段，全消费方复活（补 dict 形状回归测试）
- `platform_pub_counts` 双计：同时持 bvid+extra_pub.bilibili 的条目 B站重复计数
- 抽屉 B站未勾选时 B站选项框自动灰掉禁点；平台勾选带图标+未登录警示；设置面板平台开关加登录态圆点
- `vendor/sau/logs/` 证据截图纳入 gitignore

## v1.8.7 (2025-06)
### Fixed
- **抖音 4:3 横封面漏传**：竖版存在时横版槽被 `elif` 跳过→4:3 回落黑首帧，改横竖双槽各自上传
- **抖音误标"内容由AI生成"**：上游补丁硬编码默认声明，所有稿件被强制标 AI——改为仅显式 `--declaration` 传入才设置；`extra_platforms.<p>.declaration` 配置透传
- **小红书封面假成功**：固定 sleep 后点确定+吞弹窗异常——改等预览渲染+确定键解禁+弹窗真关闭才算成
- **快手封面受理检测修正**：受理信号改真实 DOM 特征（裁剪面板出现），消假阴性；确认键解禁检查；弹窗内+封面预览双证据截图落 `data/debug` 不再随清理删除

### Added
- `extra_platforms.<p>.headed=true` sau 平台有头模式——风控验证码可人工过
- **验证码 Web 通道**：`POST /api/verify-code` + 顶栏📱按钮——抖音短信验证码喂给等待中的投递进程
- `extra_platforms.<p>.offset_min` 分平台错峰——预约发布时间相对 B站后移 N 分钟，防同稿多平台同刻发布被风控画像

## v1.8.5 ~ v1.8.6 (2025-06)
### Added
- `trim_black_intro` 片头纯黑段自动修剪（blackdetect 0.4s~8s 才动防误伤，重编码精确剪除；下载/本地导入双入口，封面也从修剪后视频出帧）
- `download_mix_lines` 每日下载跨内容线轮转均摊（每线轮流一条，耗尽让位），防单线吃满配额

### Fixed
- **调度器墙钟到点制**：`time.sleep` 间隔制被 macOS 睡眠冻结、唤醒后推迟整间隔——改分小段比对 wall-clock deadline，错过的轮次立即补跑（配合 v1.8.4 启动补轮，重启/睡眠两类丢轮全覆盖）
- 快手封面上传竞态：`set_input_files` 后固定 sleep(1) 即确认，大图还在传→封面静默丢弃回落黑首帧——改等上传预览出现+上传中指示消失才点确认，封面区截图存证

## v1.8.4 (2025-06)
### Fixed
- **调度器重启丢当轮**：启动后等满一个间隔才首轮轮询——现在启动 2 分钟先补一轮，之后正常间隔（修复反复重启导致当日自动下载不执行的问题）

## v1.8.3 (2025-06)
### Fixed
- `_smart_cover` 命中帧的日志漏记亮度分（纯显示修正，选帧逻辑本就正确）

## v1.8.2 (2025-06)
### Fixed
- **黑屏开场视频封面黑**：`_smart_cover` 智能抽帧——多候选时间点抽样+灰度亮度评分跳过黑帧(全黑兜底最亮帧)，全分辨率输出
  - 本地导入：替代死抽首帧
  - 下载：源缩略图过暗(<20)或缺失时自动重抽
  - publish 发布前：存量 ready 条目暗封面/缺失自动补救(横版+3:4竖版都重生成)

## v1.8.1 (2025-06)
### Fixed
- `extra_platforms` 示例全部默认关闭——全新安装只投 B站（原默认开快手/视频号会致未登录必失败、且造成"仅三平台"错觉）；启用路径：login-extra 扫码→enabled
- README/仓库描述标语改为全平台定位：B站内置 + sau 13 平台可选，不再框死三平台
- `dsh-index.json` 工具计数修正 19→20（hub_import 漏更）；dsh-video-hub 0.2.1

## v1.8.0 (2025-06)
### Added
- **Web 端 B站扫码登录**：顶栏「🔐 B站登录」弹二维码，B站APP 扫码确认即完成——复刻 BiliTV 登录流程，产出的 cookies.json 含完整 token_info，与 biliup login 格式完全兼容。**不依赖 biliup 二进制/终端**，Windows/服务器等无终端环境可登录
- `settings.line_icons` 配置项：内容线图标/显示名/徽标配色全部配置驱动 `{slug: ["emoji","名称","badge样式(可选)"]}`，`/api/stats` 下发；未配置线名 🏷+哈希配色兜底
- `cli.py doctor` 新增二进制体检：biliup/ffmpeg/ffprobe/sau venv python 解析结果逐项报告

### Fixed
- **Windows 兼容性全局修复**：`cli renew` 绕过 `_bin()` 兜底（未配置时 `Path(None)` 崩）；子进程输出统一 utf-8+errors（防中文 Windows GBK 解码炸）；`/media` URL `.as_posix()`（反斜杠致预览 404）；全部文件读写显式 utf-8；`abs_path` 支持 `~/`；`_bin` 补 `biliup.exe`+PATH 查找链
- **本地文件预览**：`download_dir` 指到 `data/` 外时预览 URL 为空——新增 `/api/items/{id}/file` 条目作用域文件端点（FileResponse 支持 Range 拖动），data/ 外路径自动回退
- **开源版隐私净化**：example sources 整段注释化（无真实频道 URL/搜索词）、missions.match/字幕注释关键词中性化、metagen 内置 LINE_STYLE 通用化（个人风格走私有 `llm.line_styles`）、前端不再内置线名预设（幽灵选项清除）、官网演示文案去真实稿件号/品类词
- 示例源 `enabled: true`→注释模板：全新安装不再自动抓示例内容
- 官网 0-1 重做：浅色工程文档风，内容按真实功能重写

### Changed
- README：B站登录首选 Web 扫码；Windows 完整运行指引（exe/PATH/ffmpeg/任务计划程序）
- `requirements.txt` 补 `qrcode>=8.0`

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
