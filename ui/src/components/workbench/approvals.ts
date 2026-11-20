import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'
import { writeSessionHeaders } from './writeSession'

export type ApprovalOutcome = 'pending' | 'approved' | 'rejected' | 'timeout'
export interface ApprovalItem {
  approval_id: string
  decision_id: string
  run_id: string
  title: string
  tool_name: string
  risk: 'high' | 'mid' | 'low'
  args_digest: string
  scope: string
  requested_at: string
  expires_at: string
  revision: string
  status: ApprovalOutcome
  can_respond: boolean
  timeout_policy: 'DENY' | 'ALLOW'
  response: null | { approver_id: string; approve_ts: string; approved_args_digest: string; approve_result: 'approve' | 'reject' | 'timeout'; content_hash: string | null; note: string }
}
export interface ApprovalAnswer {
  result: 'approve' | 'reject'
  note: string
  approved_args_digest: string
  expected_revision: string
  request_id: string
}
export class ApprovalError extends Error {
  constructor(message: string, public code = 0) { super(message) }
}
const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
export function parseApprovals(value: unknown): ApprovalItem[] {
  if (!Array.isArray(value)) throw new ApprovalError('审批数据格式不正确，请刷新后重试。')
  const ids = new Set<string>()
  for (const item of value) {
    if (!isObject(item) || !['approval_id', 'decision_id', 'run_id', 'title', 'tool_name', 'args_digest', 'scope', 'revision'].every(key => typeof item[key] === 'string' && !!item[key]) ||
      !['high', 'mid', 'low'].includes(String(item.risk)) || !['pending', 'approved', 'rejected', 'timeout'].includes(String(item.status)) ||
      typeof item.can_respond !== 'boolean' || !['DENY', 'ALLOW'].includes(String(item.timeout_policy)) || (item.risk === 'high' && item.timeout_policy !== 'DENY') ||
      typeof item.requested_at !== 'string' || !Number.isFinite(Date.parse(item.requested_at)) || typeof item.expires_at !== 'string' || !Number.isFinite(Date.parse(item.expires_at)) || Date.parse(item.expires_at) <= Date.parse(item.requested_at) || ids.has(String(item.approval_id))) throw new ApprovalError('审批数据不符合约定，已停止展示。')
    if (item.status === 'pending' && item.response !== null) throw new ApprovalError('待处理审批不应包含处理结果。')
    if (item.status !== 'pending') {
      const response = item.response
      if (!isObject(response) || !['approver_id', 'approve_ts', 'approved_args_digest', 'note'].every(key => typeof response[key] === 'string') || !Number.isFinite(Date.parse(String(response.approve_ts))) ||
        response.approve_result !== ({ approved: 'approve', rejected: 'reject', timeout: 'timeout' } as Record<string, string>)[String(item.status)] || response.approved_args_digest !== item.args_digest || (response.content_hash !== null && typeof response.content_hash !== 'string')) throw new ApprovalError('审批结果与参数不匹配，已停止展示。')
    }
    ids.add(String(item.approval_id))
  }
  return value as ApprovalItem[]
}
export function createApprovalDemo(now = Date.now()): ApprovalItem[] {
  return [{ approval_id: 'APPROVAL-DEMO-001', decision_id: 'DECISION-APPROVAL-DEMO', run_id: 'RUN-APPROVAL-DEMO', title: '发布代码至测试远端', tool_name: 'git_push', risk: 'high',
    args_digest: JSON.stringify({ remote: 'test-origin', branch: 'demo/review', force: false }, null, 2), scope: 'workspace/demo-repository；仅推送测试分支，不允许强制推送',
    requested_at: new Date(now).toISOString(), expires_at: new Date(now + 30000).toISOString(), revision: 'demo-1', status: 'pending', can_respond: true, timeout_policy: 'DENY', response: null }]
}
async function apiRequest(path: string, signal: AbortSignal, answer?: ApprovalAnswer): Promise<unknown> {
  let sessionHeaders = {}
  if (answer) { try { sessionHeaders = await writeSessionHeaders(signal) } catch (cause) { if (signal.aborted) throw cause; throw new ApprovalError(cause instanceof Error ? cause.message : '无法核验会话，本次操作尚未提交。') } }
  const response = await fetch(path, { signal, credentials: 'same-origin', cache: 'no-store', method: answer ? 'POST' : 'GET', headers: { Accept: 'application/json', ...sessionHeaders, ...(answer ? { 'Content-Type': 'application/json', 'Idempotency-Key': answer.request_id } : {}) }, ...(answer ? { body: JSON.stringify(answer) } : {}) })
  if (!response.ok) {
    const messages: Record<number, string> = { 401: '登录已失效，请重新登录后重试。', 403: '当前账号没有审批权限。', 404: '审批不存在或服务接口尚未提供。', 409: '审批已变化或被他人处理，请刷新核对，系统不会自动重提。', 410: '审批已过期，请刷新查看服务端处置。' }
    throw new ApprovalError(messages[response.status] ?? '审批服务暂时不可用，请重试。', response.status)
  }
  return response.json()
}
export function useApprovals() {
  const mode = ref<'preview' | 'api'>('api')
  const items = shallowRef<ApprovalItem[]>([])
  const loading = ref(false), submitting = ref(false), error = ref(''), stale = ref(false), now = ref(Date.now())
  let demo = createApprovalDemo()
  let revision = 0
  let controller: AbortController | undefined
  const ticker = setInterval(() => { now.value = Date.now() }, 250)
  const pendingCount = computed(() => items.value.filter(item => item.status === 'pending' && Date.parse(item.expires_at) > now.value).length)
  function expired(item: ApprovalItem) { return item.status === 'pending' && Date.parse(item.expires_at) <= now.value }
  async function load() {
    const token = ++revision
    controller?.abort(); controller = new AbortController()
    const active = controller
    items.value = []; loading.value = true; error.value = ''; stale.value = false
    const timer = setTimeout(() => active.abort(), 12000)
    try {
      const result = mode.value === 'preview' ? demo : parseApprovals(await apiRequest('/api/v27/approvals', active.signal))
      if (token === revision) items.value = result
    } catch (cause) { if (token === revision) error.value = cause instanceof ApprovalError ? cause.message : '读取审批失败或超时，请检查连接后重试。' }
    finally { clearTimeout(timer); if (token === revision) loading.value = false }
  }
  async function answer(item: ApprovalItem, result: 'approve' | 'reject', note: string, checked: boolean): Promise<boolean> {
    if (submitting.value) return false
    error.value = ''; now.value = Date.now()
    const live = items.value.find(value => value.approval_id === item.approval_id)
    if (!live || live.revision !== item.revision || live.status !== 'pending') { error.value = '审批已变化，请刷新后重新核对。'; return false }
    if (stale.value || expired(item) || item.status !== 'pending') { error.value = '请求已变化或已到期，请刷新后核对。'; return false }
    if (!item.can_respond) { error.value = '当前账号没有审批权限。'; return false }
    if (!checked) { error.value = '请先确认已核对操作范围和参数。'; return false }
    if (result === 'reject' && !note.trim()) { error.value = '拒绝时请填写原因。'; return false }
    submitting.value = true
    const token = revision, active = new AbortController()
    controller = active
    const timer = setTimeout(() => active.abort(), 12000)
    try {
      let updated: ApprovalItem
      if (mode.value === 'preview') {
        updated = { ...item, status: result === 'approve' ? 'approved' : 'rejected', revision: 'demo-2', response: { approver_id: 'LOCAL-DEMO-USER', approve_ts: new Date().toISOString(), approved_args_digest: item.args_digest, approve_result: result, content_hash: null, note: note.trim() || '已确认操作范围。' } }
        demo = demo.map(value => value.approval_id === item.approval_id ? updated : value)
      } else {
        const payload: ApprovalAnswer = { result, note: note.trim(), approved_args_digest: item.args_digest, expected_revision: item.revision, request_id: crypto.randomUUID() }
        updated = parseApprovals([await apiRequest(`/api/v27/approvals/${encodeURIComponent(item.approval_id)}/respond`, active.signal, payload)])[0]
        if (updated.approval_id !== item.approval_id || updated.decision_id !== item.decision_id || updated.run_id !== item.run_id || updated.args_digest !== item.args_digest || updated.status !== (result === 'approve' ? 'approved' : 'rejected')) throw new ApprovalError('应答结果与原请求不符，请刷新核对。', 409)
        if (updated.tool_name !== item.tool_name || updated.scope !== item.scope || updated.risk !== item.risk || updated.requested_at !== item.requested_at || updated.expires_at !== item.expires_at || updated.timeout_policy !== item.timeout_policy) throw new ApprovalError('审批回执改变了工具、范围或时限，请刷新重新核对；不能据此确认授权成功。', 409)
      }
      if (token !== revision) return false
      items.value = items.value.map(value => value.approval_id === updated.approval_id ? updated : value)
      return true
    } catch (cause) {
      if (token === revision) { error.value = cause instanceof ApprovalError ? cause.message : '提交结果未知，请先刷新确认是否已处理，避免重复审批。'; stale.value = true }
      return false
    } finally { clearTimeout(timer); submitting.value = false }
  }
  watch(mode, load, { immediate: true })
  onScopeDispose(() => { revision++; controller?.abort(); clearInterval(ticker) })
  return { mode, items, loading, submitting, error, stale, now, pendingCount, expired, load, answer }
}
