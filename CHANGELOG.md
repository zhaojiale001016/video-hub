# Changelog

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
