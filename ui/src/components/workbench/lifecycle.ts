import { parseRunPage, type RunSummary } from './runs'
import type { Framework } from './model'
import { writeSessionHeaders } from './writeSession'

export interface RunContext {
  source: 'ledger'; revision: string; can_create: boolean
  workspaces: { id: string; path: string }[]
  frameworks: { name: Framework; version: string; available: boolean; reason: string }[]
  skills: { id: string; version: string }[]
}
export interface CreateRunInput { task_id: string; title: string; workspace_id: string; framework: Framework; skill_id: string; skill_version: string }
export interface LifecycleSnapshot {
  source: 'ledger'; revision: string; task_id: string; workspace_id: string; skill_id: string; skill_version: string
  run: RunSummary; can_close: boolean; can_resume: boolean; reason: string
}
export interface LifecycleReceipt {
  source: 'ledger'; action: 'create' | 'close' | 'resume'; request_id: string; audit_id: string; created: boolean; snapshot: LifecycleSnapshot
}
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
const ensure: (condition: unknown) => asserts condition = condition => { if (!condition) throw new Error('运行生命周期响应不符合契约，请刷新核对。') }
export function parseRunContext(value: unknown): RunContext {
  ensure(object(value) && value.source === 'ledger' && text(value.revision) && typeof value.can_create === 'boolean' && Array.isArray(value.workspaces) && Array.isArray(value.frameworks) && Array.isArray(value.skills))
  ensure(value.workspaces.every(item => object(item) && text(item.id) && text(item.path)) && new Set(value.workspaces.map(item => item.id)).size === value.workspaces.length)
  const names = ['openJiuwen', 'LangGraph', 'OpenAI Agents SDK']
  ensure(value.frameworks.length === 3 && value.frameworks.every(item => object(item) && names.includes(String(item.name)) && text(item.version) && typeof item.available === 'boolean' && text(item.reason) && (item.name !== 'openJiuwen' || !item.available || item.version === '0.1.20.post1')) && new Set(value.frameworks.map(item => item.name)).size === 3)
  ensure(value.skills.every(item => object(item) && text(item.id) && text(item.version)) && new Set(value.skills.map(item => JSON.stringify([item.id, item.version]))).size === value.skills.length)
  return value as unknown as RunContext
}
export function parseLifecycle(value: unknown, runId?: string): LifecycleSnapshot {
  ensure(object(value) && value.source === 'ledger' && ['revision', 'task_id', 'workspace_id', 'skill_id', 'skill_version'].every(key => text(value[key])) && typeof value.can_close === 'boolean' && typeof value.can_resume === 'boolean' && typeof value.reason === 'string')
  const run = parseRunPage({ source: 'ledger', items: [value.run], next_cursor: null }).items[0]
  ensure(!runId || run.id === runId)
  ensure(run.state !== 'closed' || (!value.can_close && !value.can_resume))
  ensure(!value.can_resume || text(value.reason))
  return value as unknown as LifecycleSnapshot
}
export function validateCreate(input: CreateRunInput, context: RunContext): string {
  if (!context.can_create) return '当前账号没有创建运行的权限。'
  if (!input.task_id.trim() || input.task_id.length > 160) return '请填写不超过 160 字的稳定业务任务标识。'
  if (!input.title.trim() || input.title.length > 2000) return '请填写不超过 2000 字的任务描述。'
  if (!context.workspaces.some(item => item.id === input.workspace_id)) return '请选择服务端授权的工作目录。'
  if (!context.frameworks.some(item => item.name === input.framework && item.available)) return '所选框架当前不可用，请刷新接入状态。'
  if (!context.skills.some(item => item.id === input.skill_id && item.version === input.skill_version)) return '请选择有效的 Skill 版本。'
  return ''
}
export function parseLifecycleReceipt(value: unknown, action: LifecycleReceipt['action'], requestId: string, expected: CreateRunInput | LifecycleSnapshot): LifecycleReceipt {
  ensure(object(value) && value.source === 'ledger' && value.action === action && value.request_id === requestId && text(value.audit_id) && typeof value.created === 'boolean')
  const snapshot = parseLifecycle(value.snapshot, 'run' in expected ? expected.run.id : undefined)
  ensure((['task_id', 'workspace_id', 'skill_id', 'skill_version'] as const).every(key => snapshot[key] === expected[key]))
  if ('run' in expected) {
    const original = expected.run, run = snapshot.run
    ensure(!value.created && original.state !== 'closed' && run.framework === original.framework && run.title === original.title && run.started_at === original.started_at && run.parent_run_id === original.parent_run_id)
    ensure(action === 'close' ? expected.can_close && run.state === 'closed' && run.segment === original.segment : action === 'resume' && expected.can_resume && run.state === original.state && run.segment === original.segment + 1 && !snapshot.can_resume)
    ensure(snapshot.revision !== expected.revision)
  } else {
    ensure(action === 'create' && snapshot.run.framework === expected.framework)
    if (value.created) ensure(snapshot.run.state === 'open' && snapshot.run.segment === 1 && snapshot.run.title === expected.title && snapshot.run.parent_run_id === null && snapshot.run.child_run_ids.length === 0 && snapshot.run.decision_count === 0)
  }
  return value as unknown as LifecycleReceipt
}
export async function lifecycleRequest(path: string, signal: AbortSignal, body?: Record<string, unknown>): Promise<unknown> {
  const sessionHeaders = body ? await writeSessionHeaders(signal) : {}
  let response: Response
  try { response = await fetch(path, { signal, credentials: 'same-origin', cache: 'no-store', method: body ? 'POST' : 'GET', headers: { Accept: 'application/json', ...sessionHeaders, ...(body ? { 'Content-Type': 'application/json', 'Idempotency-Key': String(body.request_id) } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}) }) }
  catch { throw new Error(body ? '提交连接中断，操作结果未知。请先查询或刷新运行账本核对，勿重复提交。' : '无法连接运行服务，请检查连接后重试。') }
  if (!response.ok) throw new Error(({ 401: '登录已失效，请在设置中登录后刷新。', 403: '当前账号无权执行此运行操作。', 404: '运行或生命周期接口尚不可用。', 409: '运行状态或配置已变化，请刷新核对，系统不会自动重复提交。', 422: '当前运行不满足操作条件，请刷新查看原因。' } as Record<number, string>)[response.status] ?? '运行服务暂时不可用，请刷新后核对操作结果。')
  try { return await response.json() }
  catch { throw new Error(body ? '服务响应无法解析，不能确认操作结果。请先刷新账本核对。' : '运行服务未返回有效数据，请重新读取。') }
}
