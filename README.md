# video-hub · 多平台搬运流水线

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg)]()
[![Release](https://img.shields.io/github/v/release/zhaojiale001016/video-hub)](https://github.com/zhaojiale001016/video-hub/releases)
[![CI](https://github.com/zhaojiale001016/video-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/zhaojiale001016/video-hub/actions)

外网优质视频 → 自动下载 → 字幕翻译烧录 → LLM 生成中文标题/简介 → Web 管理台审核 → 一键发布 **B站 / 快手 / 视频号**（三平台独立勾选）。

## 流程

```
config/sources.yaml 订阅源(YouTube频道/搜索)
   │
cli.py poll          轮询发现新视频 → 撞稿预检(相似≥85%直接跳过) → status=new
   │
cli.py download      yt-dlp 下载 → 语音探测/转写/翻译/烧字幕 → LLM元数据 → status=ready
   │
cli.py serve         Web管理台: 预览、编辑标题标签、选发布平台、一键发布
   │
cli.py publish <id>  biliup投B站 → 成功后 sau 投递快手/视频号(可选、尽力而为)
```

## 常用命令

```bash
cd ~/video-hub        # 项目目录

# ── 服务常驻（macOS launchd, 登录自启+崩溃自愈; Linux 用 deploy/video-hub.service.example 的 systemd 单元）──
launchctl kickstart -k gui/$(id -u)/com.videohub.pipeline   # 重启服务
launchctl print    gui/$(id -u)/com.videohub.pipeline       # 查看运行状态
launchctl bootout  gui/$(id -u)/com.videohub.pipeline       # 停止
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.videohub.pipeline.plist  # 重新载入
#   plist 模板: deploy/com.videohub.pipeline.plist.example

# ── 日常 ──
.venv/bin/python3 cli.py doctor        # 体检: cookie/登录态/队列/今日额度
.venv/bin/python3 cli.py jobs          # 查看任务日志
.venv/bin/python3 cli.py reconcile     # 调和: 清理发布残留/提示中途断档条目
.venv/bin/python3 cli.py verify        # 巡检: 已发布稿件存活状态
.venv/bin/python3 cli.py stats         # 今日额度/磁盘/登录概览

# ── 手动流水线 ──
.venv/bin/python3 cli.py poll                 # 拉新视频
.venv/bin/python3 cli.py download             # 批量下载+处理(占当日额度)
.venv/bin/python3 cli.py download --id 123    # 单条续跑/手动下载(不占额度)
.venv/bin/python3 cli.py publish 123          # 单条发布(B站+勾选的多平台)
.venv/bin/python3 cli.py import ~/V/x.mp4     # 本地文件入库→字幕+元数据→ready(不占额度)
.venv/bin/python3 cli.py import ~/V/x.mp4 --line music --no-process   # 指定内容线/只入库

# ── 登录态 ──
# B站首选: Web 管理台顶栏「🔐 B站登录」弹二维码, B站APP扫(无需终端/二进制,
# Windows/服务器也能登; 写 cookies.json 与 biliup 格式完全兼容)
.venv/bin/python3 cli.py login         # B站扫码(终端版, 需已装 biliup)
.venv/bin/python3 cli.py renew         # B站 cookie 刷新
.venv/bin/python3 cli.py login-ks      # 快手扫码(浏览器弹窗)
.venv/bin/python3 cli.py login-sph     # 视频号扫码
.venv/bin/python3 cli.py login-extra douyin   # 任意 sau 平台扫码(抖音/小红书/百家号…)
.venv/bin/python3 cli.py missions      # 查看可参加的投稿活动
```

管理台：**http://127.0.0.1:8600**

## 快速开始

```bash
# 1. 环境（Python ≥3.11 + ffmpeg + 字体见下）
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. 配置文件
cp config/sources.example.yaml config/sources.yaml   # 首启缺省时也会自动复制
#   改配置建议全程走 Web「⚙ 设置」→ 写入 config/overrides.json,
#   sources.yaml 保持出厂便于升级；两边都做深合并(源列表为整体替换)

# 3. 安装 biliup（B站投稿器, 第三方二进制不入库）
#   https://github.com/biliup/biliup-rs/releases → 下载对应平台二进制
#   放到 bin/ 下: bin/biliup (mac/Linux, chmod +x) 或 bin/biliup.exe (Windows)
#   也会自动找 PATH 里的 biliup; 自定义路径可在设置 biliup_bin 里改

# 4. sau 投递引擎（快手/抖音/视频号等13平台; 只用B站可跳过）
cd vendor/sau && python3 -m venv .venv && .venv/bin/pip install -e . \
  && .venv/bin/patchright install chromium && cd ../..
#   国内拉浏览器慢可加镜像: PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright

# 5. 登录（扫码，终端跑）
.venv/bin/python3 cli.py login                  # B站
.venv/bin/python3 cli.py login-extra douyin     # 任意 sau 平台(快手 kuaishou / 视频号 tencent ...)
#   login-ks / login-sph 是快手/视频号的兼容别名

# 6. 跑起来
.venv/bin/python3 cli.py serve --port 8600   # 或交给 launchd 常驻(见下)
.venv/bin/python3 cli.py doctor              # 体检
```

## 依赖清单

| 依赖 | 用途 | 安装 |
|---|---|---|
| Python ≥3.11 | 主程序 | 系统/pyenv |
| ffmpeg | 音频探测/字幕烧录 | `brew install ffmpeg` |
| whisper.cpp / faster-whisper | 语音探测+转写 | `pip install` 随 requirements |
| bin/biliup | B站投稿 | biliup-rs releases 手动放 bin/ |
| vendor/sau + 其 .venv | 快手/视频号投递 | 见 vendor/sau/README(上游 social-auto-upload) |
| LLM | 标题/简介/标签 | 默认 kscc CLI；或 api 后端 + `export LLM_API_KEY` |
| 中文字体 | 烧录字幕 | macOS 自带 PingFang；Linux `fonts-noto-cjk`；Windows 自带 msyh |
| 代理 | YouTube 下载 | `settings.proxy` 或 `https_proxy` |

> **平台支持**：macOS 为主开发平台，全部功能验证过。Linux 可用（字体/通知/常驻已适配，见 deploy/systemd 示例）。Windows 未实测（`_sau_py` 已兼容 Scripts 路径，其余以社区反馈为准）。

## 目录

| 路径 | 作用 |
|---|---|
| `config/sources.yaml` | **本地私有**（gitignore）：出厂底包配置；首启从 example 复制 |
| `config/sources.example.yaml` | 配置模板(入库) |
| `config/overrides.json` | **本地私有**（gitignore）：UI 写入的覆盖层，与 yaml 深合并 |
| `config/tids.json` | B站分区号映射 |
| `bin/biliup` | biliup-rs 投稿工具(B站) |
| `pipeline/` | 轮询/下载/字幕/元数据/投稿/多平台投递模块 |
| `pipeline/multipub.py` | 快手/视频号投递(经 vendor/sau 子进程) |
| `vendor/sau/` | social-auto-upload(快手/视频号引擎,独立venv) |
| `web/` | FastAPI 管理台 |
| `data/` | sqlite 队列 + 下载文件 + cookies + server.log |

## 多平台发布（平台平等 + 可扩展）

- **B站 + extra_platforms 里启用的所有平台是平等的可选目标**；抽屉勾选框按 `/api/platforms` 动态生成，无写死平台
- `extra_platforms` 支持 **sau 全部视频平台**（13 个）：`kuaishou` 快手 / `tencent` 视频号 / `douyin` 抖音 / `xiaohongshu` 小红书 / `baijiahao` 百家号 / `weibo` 微博 / `youtube` / `facebook-page` Facebook主页 / `instagram` / `alipay` 支付宝 / `hupu` 虎扑 / `dayu` 大鱼号 / `zhihu` 知乎。新增平台：设置面板勾选 → `cli.py login-extra <平台id>` 扫码登录。各平台能力有差异（封面/定时参数按 sau 支持自动适配，知乎/FB主页不支持封面，微博/小红书…不支持定时）
- `pub_platforms` 语义：空/`""`=启用平台全发；`["kuaishou"]`=只发快手(B站完全不碰)；`[]`=拒绝发布
- 不勾 B站的稿件跳过全部 B站域流程（撞稿预检/活动匹配/biliup/bvid），只走 sau 多平台投递；≥1 成功即 `published`
- 每条 `extra_pub` JSON 记录**逐平台结果**（含B站），抽屉里以徽标展示成功/失败/未勾选；重试/补投自动跳过已成功平台，文件在全平台成功前保留
- `sources.yaml` 的 `extra_platforms` 里 `enabled` 为总开关；条目勾选是在启用平台上的过滤
- sau 引擎独立 venv 隔离，快手上传超时已调 1500s（vendored 改动，`git pull` 会被覆盖需重补）

## 版权类型（重要）

- `default_copyright: 1` 自制 / `2` 转载；卡片上每条可单独改
- **转载**必须填 `source_url` 且**不能参加活动**（B站硬规则 code 21071）
- **自制**无 source 要求、活动匹配自动生效、流量权重高——但声明"这是你做的"，被举报查实会下架扣分；烧录中文字幕是目前的加工缓冲
- 简介为纯内容介绍 + 结尾「视频仅供学习交流，如有侵权请联系删除」，不含来源链接/原作者（转载稿的出处由投稿表单 `--source` 字段体现）；DB 中 `source_url` 仍留档

## 养号守则

- 新号每日 1-3 条，`daily_upload_limit` 控制上限；短时间连投会触发B站限流(code 21566)，用「错峰定时发布」
- 被退稿/下架是常态，`failed` 里看原因，高危源就 disable
- cookies 几个月会过期：`cli.py renew` / `login-ks` / `login-sph` 重新登录
- YouTube 风控出验证码 → 导出 cookies.txt 填 `cookies_file`

## 投稿活动

- **仅自制稿(copyright=1)可参加**，转载稿 B站直接拒绝(21071)
- 发布前按业务线关键词(`missions.match`)自动匹配活动，话题 tag 自动并入标签
- `cli.py missions` 手动查看；卡片"活动ID"框可手动指定（空=自动）
- 创作激励/电磁力等账号级计划无需配置，账号开通后投稿自动累计

## 投稿命令等价物

```
biliup -u data/cookies.json upload video.mp4 \
  --copyright 1 --tid 29 \
  --title "标题" --desc "简介" --tag a,b,c --cover cover.jpg --submit web
# 转载稿: --copyright 2 --source <原链接> (不可加 --mission-id)
# 自制稿可附带: --mission-id <活动id>
```

## 中文字幕（生肉→熟肉）

```yaml
subtitles:
  enabled: true            # 下载后自动生成中文字幕并烧入画面
  whisper_model: "small"   # 探测+转写共用, 模型存 data/models/hf
  whisper_device: "mps"    # Apple Silicon GPU; 不稳改回 "cpu"
  burn: true               # 烧进画面(B站不认外挂srt)
  font_size: 0             # 0=按高度自适应(1080p≈60, 720p≈40)
  margin_v: 0              # 0=自适应(画面高7.5%); >0=固定px
  policy:                  # 业务线默认策略; 源上可加 subtitle: 字段覆盖
    music: "subs_only"     # 只翻官方字幕(歌词whisper转写不可靠)
    relax: "auto"          # 官方字幕→翻译; 没有→探测有语音才转写
```

- 策略取值：`always` 强制whisper转写 | `auto` 探测有语音才配 | `subs_only` 仅翻译官方字幕 | `never` 不配
- 探测逻辑：whisper 对前3分钟试转写（VAD 过滤幻觉），字符/分钟 ≥60 判定有解说才全片转写
- 典型配置：纯ASMR/无解说源 `subtitle: "never"`；全程旁白解说的源 `"always"`
- 烧录样式：底部居中白字黑边，字幕组惯例字号≈画面高1/18、距底≈7.5%高度，`margin_v`/`font_size` 可覆盖
- 注意：硬字幕不可逆；whisper 对唱歌/纯音乐不可靠（音乐线默认只翻官方字幕）

## 发布前自动校验

- **撞稿预检**：用标题搜B站，相似度≥72% 判撞稿拦截；自动排除自己已发的稿；`--force`/卡片「强制发」可绕过
- **标题撞车**：与本地已发布稿件同名的直接拦下
- **转载校验**：copyright=2 必须有 source_url；标题≤80字、简介≤1500字自动截断

## 定时与调度

- 服务由 **launchd 常驻托管**（`~/Library/LaunchAgents/com.videohub.pipeline.plist`）：登录自启 + 崩溃自愈；重启前先确认页面无在跑任务
- `publish_slots: ["12:00","18:00","19:30","21:00","22:30"]` — 「错峰定时发布」自动分稿到 ≥4h 的档位（快手/视频号同步对齐该时刻）
- `auto_poll_interval_min: 360` + `auto_download_after_poll: true` — 每 6h 自动拉新+下载+处理
- `auto_workdays_only: true` — **仅工作日自动执行**，周末整 tick 跳过（手动按钮不受影响）
- `whisper_device: mps` — Apple Silicon GPU 转写（约2x速，初始化失败自动回退 cpu）
- `delete_after_publish` — **全部勾选平台投递成功**才删本地视频；部分成功保留文件供补投，也可在抽屉里手动「🗑 清理文件」（跳过条目总是删文件、留去重记录）
- `cli.py doctor` — 体检：cookie/登录态/活动数/队列/今日已投

## 防重复 & 每日额度

**五层防重**（已发视频绝不可能再下载/重投）：
1. DB 唯一键 `UNIQUE(platform, source_id)` —— 同一视频(按视频ID)只入队一次，频道源和搜索源命中同一视频也会去重
2. yt-dlp `download_archive` —— 下载过的视频即使重置状态也跳过下载
3. 已 `published` 的条目永远不再进入下载/发布流程
4. 发布前撞稿预检：搜B站比对相似度≥72% 拦截（防别人已搬过的）
5. 同名标题拦截：与已发布稿件同名的直接拒绝

**额度**：
- `daily_download_limit: 5` —— 批量下载/自动调度每天最多处理 5 条，额度用完自动停，明天继续
- `daily_upload_limit: 5` —— 每日投稿上限
- 手动触发不受影响：**卡片上的「下载」按钮** / `cli.py download --id <id>` 想下哪条下哪条，不占额度

**手动 vs 自动的区别**（仅此两点）：
| | 自动(调度器/批量按钮) | 手动(卡片下载按钮/--id) |
|---|---|---|
| 每日额度 | 受 5 条/天限制 | 不受限 |
| 处理范围 | 按队列顺序取 new | 指定单条 |
| 流水线 | 完全相同：下载→字幕判定→LLM标题→ready | 相同 |

其余（失败重试、撞稿预检、活动匹配、转载来源）手动自动完全一致。

## 给 AI Agent 用（DSH 插件 / MCP / Skill / 裸 API 四选一）

video-hub 不止有 Web 管理台——四种方式让 AI 工具直接驱动整条流水线：

**1. DeepSeek Harness 原生插件（dsh 用户首选）**

`plugins/dsh-video-hub/` 是标准 dsh bundle 包，装到你的 DSH 后 20 个 `hub_*` 工具进原生工具系统（非外挂 MCP），会话里直接说"把 ready 队列发掉""1372 改个标题"：

- **插件市场**：DSH Plugins → 开「Custom plugin indexes」→ Discover 添加源 `https://zhaojiale001016.github.io/video-hub/dsh-index.json` → 搜 video-hub 一键安装
- **命令行**：`dsh plugin --profile <p> add 'zhaojiale001016/video-hub#path:/plugins/dsh-video-hub'`
- **本地**：`dsh plugin --profile <p> add ./plugins/dsh-video-hub`

装完重启 DSH 生效。默认连 `http://127.0.0.1:8600`，改端口在 profile 的 `cordis.patch.yml`
覆盖 `video-hub` 条目 `config.api`，或设 `VIDEOHUB_API` 环境变量。
详见 [plugins/dsh-video-hub/README.md](plugins/dsh-video-hub/README.md)。

**2. MCP Server（一次接入全 MCP 生态）**

`mcp_server.py` 是零依赖 stdio MCP server（只需 httpx），暴露 20 个 `hub_*` 工具。
要求管理服务在跑（`cli.py serve`/常驻）。客户端配置：

```json
{"mcpServers": {"video-hub": {
  "command": "/path/to/video-hub/.venv/bin/python3",
  "args": ["/path/to/video-hub/cli.py", "mcp"]}}}
```

配进 `claude_desktop_config.json` / `.mcp.json` / Cursor / Devin / Windsurf 等任意 MCP 客户端，
agent 即可查队列、改元数据、发布、补投、加源、追任务进度。

**3. Agent Skill（免 MCP 配置的轻量方案）**

`skills/video-hub/SKILL.md` 是通用 skill 定义——教会 agent 用 REST API/CLI 驱动中台。
安装：复制或软链到对应目录即可——

```bash
ln -s ~/video-hub/skills/video-hub ~/.claude/skills/video-hub   # Claude Code
ln -s ~/video-hub/skills/video-hub ~/.codex/skills/video-hub    # Codex
ln -s ~/video-hub/skills/video-hub ~/.devin/skills/video-hub    # Devin
```

**4. 裸 API / CLI**

FastAPI 自带 OpenAPI：`http://127.0.0.1:8600/openapi.json`（在线文档 `/docs`），
agent 可自行生成调用；CLI 全功能 `cli.py --help`。给 coding agent 的仓库导引见 [AGENTS.md](AGENTS.md)。

## 测试与发布

```bash
.venv/bin/python -m pytest tests/ -q     # 核心逻辑单测(平台选择/命令构造/DB/相似度)
```

发布流程：`git tag vX.Y.Z && git push --tags` → `gh release create`。版本历史见 [CHANGELOG.md](CHANGELOG.md)。

## 法律与免责（务必阅读）

本项目是**第三方内容的中转/发布工具**。使用前请自行确认：

- 你对搬运的视频拥有合法使用权（原作者授权/CC 许可/合理使用抗辩），并遵守来源站与各发布平台的条款
- 简介中的「如有侵权请联系删除」不是免责金牌——**收到权利人通知必须立即配合下架**
- B站投稿选「转载」必须如实填写出处；选「自制」构成原创声明，被查实搬运会下架扣分甚至封号
- 请尊重原作者：保留可识别的作者信息、不冒充原创、不在原作者已入驻的平台抢发其内容
- 本项目按 MIT 协议提供，使用者对自身发布行为负全部责任

## 隐私与安全

- 所有 cookie/凭证存于 `data/`（已 gitignore），**绝不入库**；代理串里的账密在 API 回显时自动打码
- 管理台无鉴权——**仅监听 127.0.0.1**；如需暴露到局域网请先自行加反代鉴权
- `config/sources.yaml`、`config/overrides.json`、`data/` 整目录均为本地私有
