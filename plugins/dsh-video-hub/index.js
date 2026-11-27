/**
 * dsh-video-hub — video-hub 视频搬运中台的 DeepSeek Harness 插件
 *
 * 把本机 video-hub 管理台(默认 http://127.0.0.1:8600)的能力注册为
 * dsh LLM 工具: 队列/发布/补投/编辑/加源/平台登录/验证码/任务进度, 共 23 个 hub_*。
 * 前置: video-hub 服务在跑 (cli.py serve / launchd / systemd 常驻)。
 * 配置: 本包 cordis.patch.yml 里 config.api, 或环境变量 VIDEOHUB_API。
 */

export const name = 'dsh-video-hub'
export const inject = ['tools']

let API = 'http://127.0.0.1:8600'

const toJson = (x) => JSON.stringify(x, null, 1)

async function call(method, path, { params, body, timeout = 30000 } = {}) {
  try {
    const url = new URL(API + path)
    for (const [k, v] of Object.entries(params || {})) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    }
    const r = await fetch(url, {
      method,
      headers: body ? { 'content-type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(timeout),
    })
    const data = await r.json().catch(() => ({}))
    if (!r.ok) return { error: data.detail || `HTTP ${r.status}` }
    return data
  } catch (e) {
    return {
      error: `video-hub 管理服务不可达(${API})——先启动 cli.py serve 或确认常驻服务, ${e.message || e}`,
    }
  }
}

// JSON Schema 工具(与 dsh 运行时 tools.register 的 parameters 契约一致)
const S = (description) => ({ type: 'string', description })
const I = (description) => ({ type: 'integer', description })
const B = (description) => ({ type: 'boolean', description })
const params = (properties, required = []) => ({
  type: 'object', properties, required, additionalProperties: false,
})

const TOOLS = [
  {
    name: 'hub_stats',
    description: 'video-hub 队列概览: 各状态计数/今日上传下载额度/磁盘剩余/各平台登录态/LLM开关',
    parameters: params({}),
    execute: () => call('GET', '/api/stats'),
  },
  {
    name: 'hub_platforms',
    description: '全部可投递平台: id/名称/启用态/登录态/发稿数/创作中心链接',
    parameters: params({}),
    execute: () => call('GET', '/api/platforms'),
  },
  {
    name: 'hub_platform_login',
    description: '拉起指定 sau 平台的扫码登录浏览器(有头, 需本机有屏幕; B站走 QR API 不用此)',
    parameters: params({ pid: S('平台id, 如 douyin/kuaishou/tencent/xiaohongshu') }, ['pid']),
    execute: (a) => call('POST', `/api/platforms/${a.pid}/login`, { timeout: 15000 }),
  },
  {
    name: 'hub_platform_open',
    description: '带 sau 登录态拉起真实浏览器直达创作平台页(target: home/manage/publish); 有头, 需本机屏幕',
    parameters: params({
      pid: S('平台id'), target: S('home/manage/publish, 默认 home'),
    }, ['pid']),
    execute: (a) => call('POST', `/api/platforms/${a.pid}/open`,
      { body: { target: a.target || 'home' }, timeout: 20000 }),
  },
  {
    name: 'hub_verify_code',
    description: '抖音等平台投递触发短信/滑块验证时提交 4-8 位数字验证码(写入 sau 拾取文件, 等待中的进程自动续跑)',
    parameters: params({ code: S('验证码 4-8 位数字') }, ['code']),
    execute: (a) => call('POST', '/api/verify-code', { body: { code: String(a.code) } }),
  },
  {
    name: 'hub_progress',
    description: '正在处理条目的实时进度(下载MB/上传%/烧录阶段)',
    parameters: params({}),
    execute: () => call('GET', '/api/progress'),
  },
  {
    name: 'hub_list',
    description: '列条目。status 可逗号组合: new/downloaded/ready/published/failed/skipped; line 按内容线过滤',
    parameters: params({
      status: S('状态过滤, 默认 ready'),
      line: S('内容线 slug'),
      limit: I('条数上限, 默认 50'),
    }),
    execute: (a) => call('GET', '/api/items', { params: { status: a.status || 'ready', line: a.line, limit: a.limit || 50 } }),
  },
  {
    name: 'hub_get',
    description: '单条详情: 标题/简介/标签/extra_pub 逐平台投递结果/文件路径',
    parameters: params({ item_id: I('条目 id') }, ['item_id']),
    execute: async (a) => {
      for (const st of ['ready', 'downloaded', 'new', 'published', 'failed', 'skipped']) {
        const d = await call('GET', '/api/items', { params: { status: st, limit: 100000 } })
        for (const it of d.items || []) if (it.id === a.item_id) return it
        if (d.error) return d
      }
      return { error: `#${a.item_id} 不存在` }
    },
  },
  {
    name: 'hub_edit',
    description: '编辑稿件元数据(已发布稿件不可改)。copyright: 1=自制 2=转载; pub_platforms/tags 逗号分隔',
    parameters: params({
      item_id: I('条目 id'),
      title: S('标题 ≤80字'),
      description: S('简介'),
      tags: S('标签, 逗号分隔'),
      tid: I('B站分区号'),
      mission_id: I('B站活动 id'),
      copyright: I('1=自制 2=转载'),
      dtime: I('定时发布10位时间戳'),
      pub_platforms: S('发布平台子集, 逗号分隔(如 bilibili,kuaishou)'),
    }, ['item_id']),
    execute: (a) => {
      const body = {}
      for (const k of ['title', 'description', 'tid', 'mission_id', 'copyright', 'dtime']) {
        if (a[k] !== undefined && a[k] !== null && a[k] !== '') body[k] = a[k]
      }
      if (a.tags) body.tags = String(a.tags).split(',').map((x) => x.trim()).filter(Boolean)
      if (a.pub_platforms) body.pub_platforms = String(a.pub_platforms).split(',').map((x) => x.trim()).filter(Boolean)
      return Object.keys(body).length
        ? call('PATCH', `/api/items/${a.item_id}`, { body })
        : { error: '没有任何字段要改' }
    },
  },
  {
    name: 'hub_publish',
    description: '发布一条: 勾选平台各自投递互不阻断, 已成功平台自动跳过。force 跳撞稿预检; dtime 需≥4h后。长任务返回 job_id, 用 hub_wait_job 收尾',
    parameters: params({
      item_id: I('条目 id'),
      force: B('跳过撞稿预检'),
      dtime: I('定时发布10位时间戳'),
    }, ['item_id']),
    execute: (a) => call('POST', `/api/items/${a.item_id}/publish`,
      { params: { force: !!a.force, dtime: a.dtime || 0 }, timeout: 15000 }),
  },
  {
    name: 'hub_publish_all',
    description: '按每日限额把 ready 队列发完; schedule=true 按 publish_slots 错峰定时。返回 job_id',
    parameters: params({ schedule: B('错峰定时发布') }),
    execute: (a) => call('POST', '/api/publish-all', { params: { schedule: !!a.schedule }, timeout: 15000 }),
  },
  {
    name: 'hub_poll',
    description: '轮询全部启用订阅源拉新入队(含撞稿预检)。返回 job_id',
    parameters: params({}),
    execute: () => call('POST', '/api/poll', { timeout: 15000 }),
  },
  {
    name: 'hub_download',
    description: 'item_id>0 单条下载(不占日额度); 否则批量下载(受 daily_download_limit)。返回 job_id',
    parameters: params({ item_id: I('条目id(可选)'), limit: I('批量上限') }),
    execute: (a) => a.item_id
      ? call('POST', `/api/items/${a.item_id}/download`, { timeout: 15000 })
      : call('POST', '/api/download', { params: { limit: a.limit || 0 }, timeout: 15000 }),
  },
  {
    name: 'hub_import',
    description: '导入本地视频文件进流水线(复制入下载目录, 默认自制版权)。process=true 异步字幕+元数据→ready 返回 job_id; false 只入库停 new',
    parameters: params({
      path: S('本机视频文件绝对路径'),
      line: S('内容线 slug, 默认 local'),
      title: S('原标题(可空取文件名)'),
      process: B('是否立即处理, 默认 true'),
    }, ['path']),
    execute: (a) => call('POST', '/api/import', {
      body: { path: a.path, line: a.line || 'local', title: a.title || '',
              process: a.process !== false }, timeout: 15000 }),
  },
  {
    name: 'hub_skip',
    description: '跳过条目(删本地文件腾磁盘); undo=true 撤销并清 download archive 允许重下',
    parameters: params({ item_id: I('条目 id'), undo: B('撤销跳过') }, ['item_id']),
    execute: (a) => call('POST', `/api/items/${a.item_id}/${a.undo ? 'unskip' : 'skip'}`),
  },
  {
    name: 'hub_retry',
    description: '失败条目重试: 文件在→ready; 文件没了→回 new 并清 archive 重下',
    parameters: params({ item_id: I('条目 id') }, ['item_id']),
    execute: (a) => call('POST', `/api/items/${a.item_id}/retry`),
  },
  {
    name: 'hub_regen',
    description: 'LLM 重新生成标题/简介/标签/分区(已发布稿件不可)',
    parameters: params({ item_id: I('条目 id') }, ['item_id']),
    execute: (a) => call('POST', `/api/items/${a.item_id}/regen`, { timeout: 120000 }),
  },
  {
    name: 'hub_delete_files',
    description: '清理已发布稿件本地文件。注意: 部分平台未投完时文件是补投材料, 别删',
    parameters: params({ item_id: I('条目 id') }, ['item_id']),
    execute: (a) => call('DELETE', `/api/items/${a.item_id}/files`),
  },
  {
    name: 'hub_sources',
    description: '全部订阅源+健康档案(最近轮询时间/新增数/最近错误)',
    parameters: params({}),
    execute: () => call('GET', '/api/sources'),
  },
  {
    name: 'hub_add_source',
    description: '加订阅源。kind: channel/search; line: 任意小写内容线 slug; subtitle 覆盖线策略(always/auto/subs_only/never)',
    parameters: params({
      name: S('源名'),
      url: S('频道/搜索 URL'),
      line: S('内容线 slug'),
      kind: S('channel 或 search'),
      tid: I('B站分区号'),
      limit: I('拉取上限'),
      subtitle: S('字幕策略'),
    }, ['name', 'url', 'line']),
    execute: (a) => call('POST', '/api/sources', { body: a }),
  },
  {
    name: 'hub_jobs',
    description: '近 20 个后台任务: job_id/状态/结果摘要',
    parameters: params({}),
    execute: () => call('GET', '/api/jobs'),
  },
  {
    name: 'hub_job',
    description: '单任务详情+日志尾部',
    parameters: params({ job_id: S('任务 id') }, ['job_id']),
    execute: (a) => call('GET', `/api/jobs/${a.job_id}`),
  },
  {
    name: 'hub_wait_job',
    description: '阻塞等待任务结束(最长 timeout_s 秒)返回最终状态+结果',
    parameters: params({ job_id: S('任务 id'), timeout_s: I('最长等待秒, 默认 120') }, ['job_id']),
    execute: async (a) => {
      const deadline = Date.now() + (a.timeout_s || 120) * 1000
      while (Date.now() < deadline) {
        const j = await call('GET', `/api/jobs/${a.job_id}`)
        if (j && (j.status === 'done' || j.status === 'error')) return j
        await new Promise((r) => setTimeout(r, 3000))
      }
      return { error: '超时未结束', job_id: a.job_id }
    },
  },
]

const render = (_args, value) => [{ type: 'text', text: String(value) }]

export function apply(ctx, config) {
  API = (config && config.api) || process.env.VIDEOHUB_API || API
  const disposers = []
  for (const t of TOOLS) {
    disposers.push(ctx.tools.register({
      name: t.name,
      description: t.description,
      parameters: t.parameters,
      output: { schema: { type: 'string' }, render },
      execute: async (args) => toJson(await t.execute(args || {})),
    }))
  }
  ctx.logger?.info?.(`dsh-video-hub: ${TOOLS.length} hub_* tools → ${API}`)
  return () => { for (const d of disposers) typeof d === 'function' && d() }
}
