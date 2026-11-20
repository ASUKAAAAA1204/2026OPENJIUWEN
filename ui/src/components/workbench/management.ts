import { onScopeDispose, ref, shallowRef, watch, type Ref } from 'vue'
import { writeSessionHeaders } from './writeSession'

export const capabilityKeys = ['release_gate', 'remediation_quote', 'version_comparison'] as const
export type Capability = typeof capabilityKeys[number]
export const capabilityLabels: Record<Capability, string> = { release_gate: '发布闸', remediation_quote: '整改报价', version_comparison: '版本对比' }
export interface ManagementSnapshot {
  source: 'ledger' | 'frontend_preview'; revision: string
  capabilities: Record<Capability, { enabled: boolean; can_configure: boolean; can_execute: boolean }>
  last_change: { actor_id: string; at: string; reason: string; audit_id: string } | null
}
export interface ManagementTarget { skill_id: string; skill_version: string; policy_version: string; evaluation_revision: string }
export interface ReleaseResult {
  target: ManagementTarget; assessment_id: string; assessed_at: string; verdict: 'PASS' | 'BLOCK'
  reasons: string[]; checks: { name: string; passed: boolean; actual: string; required: string }[]
}
export interface QuoteResult {
  target: ManagementTarget; quote_id: string; created_at: string; currency: 'CNY'; total_minor: number
  items: { item_id: string; decision_id: string; action: string; basis: string; amount_minor: number }[]
}
export class ManagementError extends Error {}
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
const money = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0
const time = (value: unknown) => text(value) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))
function ensure(value: unknown): asserts value { if (!value) throw new ManagementError('管理服务响应与请求不一致，请刷新后重新核对。') }
export function parseManagement(value: unknown): ManagementSnapshot {
  ensure(object(value) && value.source === 'ledger' && text(value.revision) && object(value.capabilities))
  for (const key of capabilityKeys) {
    const item = value.capabilities[key]
    ensure(object(item) && ['enabled', 'can_configure', 'can_execute'].every(name => typeof item[name] === 'boolean'))
  }
  const change = value.last_change
  ensure(change === null || (object(change) && text(change.actor_id) && time(change.at) && text(change.reason) && text(change.audit_id)))
  return value as unknown as ManagementSnapshot
}
function sameTarget(value: unknown, target: ManagementTarget) {
  return object(value) && (Object.keys(target) as (keyof ManagementTarget)[]).every(key => value[key] === target[key])
}
export function parseRelease(value: unknown, target: ManagementTarget): ReleaseResult {
  ensure(object(value) && sameTarget(value.target, target) && text(value.assessment_id) && time(value.assessed_at) && ['PASS', 'BLOCK'].includes(String(value.verdict)) && Array.isArray(value.reasons) && value.reasons.length > 0 && value.reasons.every(text) && Array.isArray(value.checks) && value.checks.length > 0)
  for (const check of value.checks) ensure(object(check) && text(check.name) && typeof check.passed === 'boolean' && text(check.actual) && text(check.required))
  ensure(new Set(value.checks.map(check => check.name)).size === value.checks.length)
  ensure(value.verdict === 'PASS' ? value.checks.every(check => check.passed) : value.checks.some(check => !check.passed))
  return value as unknown as ReleaseResult
}
export function parseQuote(value: unknown, target: ManagementTarget): QuoteResult {
  ensure(object(value) && sameTarget(value.target, target) && text(value.quote_id) && time(value.created_at) && value.currency === 'CNY' && money(value.total_minor) && Array.isArray(value.items))
  for (const item of value.items) ensure(object(item) && ['item_id', 'decision_id', 'action', 'basis'].every(name => text(item[name])) && money(item.amount_minor))
  ensure(new Set(value.items.map(item => item.item_id)).size === value.items.length)
  const sum = value.items.reduce((total, item) => total + item.amount_minor, 0)
  ensure(money(sum) && value.total_minor === sum)
  return value as unknown as QuoteResult
}
export function createManagementDemo(): ManagementSnapshot {
  return { source: 'frontend_preview', revision: 'demo-1', capabilities: { release_gate: { enabled: false, can_configure: true, can_execute: true }, remediation_quote: { enabled: false, can_configure: true, can_execute: true }, version_comparison: { enabled: false, can_configure: true, can_execute: true } }, last_change: null }
}
export function useManagement(mode: Ref<'preview' | 'api'>, target: Ref<ManagementTarget | null>) {
  const data = shallowRef<ManagementSnapshot | null>(null), release = shallowRef<ReleaseResult | null>(null), quote = shallowRef<QuoteResult | null>(null)
  const loading = ref(false), submitting = ref(false), stale = ref(false), error = ref(''), feedback = ref('')
  let revision = 0, controller: AbortController | undefined, demo = createManagementDemo(), demoSequence = 1
  async function request(path: string, signal: AbortSignal, body?: Record<string, unknown>) {
    let sessionHeaders = {}
    if (body) { try { sessionHeaders = await writeSessionHeaders(signal) } catch (cause) { if (signal.aborted) throw cause; throw new ManagementError(cause instanceof Error ? cause.message : '无法核验会话，本次操作尚未提交。') } }
    const response = await fetch(path, { signal, credentials: 'same-origin', cache: 'no-store', method: body ? 'POST' : 'GET', headers: { Accept: 'application/json', ...sessionHeaders, ...(body ? { 'Content-Type': 'application/json', 'Idempotency-Key': String(body.request_id) } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}) })
    if (!response.ok) throw new ManagementError(({ 401: '登录已失效，请重新登录后刷新。', 403: '当前账号无权执行此管理操作，请刷新确认权限。', 404: '管理服务尚未提供此接口。', 409: '管理配置或评估快照已变化，请刷新后重新核对。', 422: '服务端拒绝此操作，请刷新后核对启用条件。' } as Record<number, string>)[response.status] ?? '管理服务暂时不可用，请刷新确认结果后重试。')
    return response.json()
  }
  function clearResults() { release.value = null; quote.value = null; feedback.value = '' }
  async function load() {
    if (submitting.value) return
    const token = ++revision
    controller?.abort(); controller = new AbortController()
    const active = controller, timer = setTimeout(() => active.abort(), 12000)
    loading.value = true; data.value = null; error.value = ''; stale.value = false; clearResults()
    try { const result = mode.value === 'preview' ? demo : parseManagement(await request('/api/v27/management', active.signal)); if (token === revision) data.value = result }
    catch (cause) { if (token === revision) error.value = cause instanceof ManagementError ? cause.message : '读取管理配置失败或超时，请检查连接后刷新。' }
    finally { clearTimeout(timer); if (token === revision) loading.value = false }
  }
  async function mutate(action: 'configure' | 'release' | 'quote', key?: Capability, enabled?: boolean, reason = '', confirmed = false) {
    if (submitting.value || loading.value) return false
    error.value = ''; feedback.value = ''
    const snapshot = data.value, boundTarget = target.value ? { ...target.value } : null
    if (!snapshot || stale.value) { error.value = '请刷新管理配置后重新核对。'; return false }
    const capability = action === 'configure' ? key! : action === 'release' ? 'release_gate' : 'remediation_quote'
    const permission = snapshot.capabilities[capability]
    if (action === 'configure' ? !permission.can_configure : !permission.can_execute || !permission.enabled) { error.value = '此能力未启用或当前账号没有操作权限。'; return false }
    if (!confirmed || (action === 'configure' && !reason.trim())) { error.value = '请核对操作范围；修改配置时还需填写变更原因。'; return false }
    if (action !== 'configure' && !boundTarget) { error.value = '请先选择可评估版本。'; return false }
    if (action === 'configure' && enabled === permission.enabled) { error.value = '配置未发生变化。'; return false }
    submitting.value = true; clearResults()
    const token = revision, active = new AbortController(); controller = active
    const timer = setTimeout(() => active.abort(), 12000)
    try {
      if (action === 'configure') {
        let updated: ManagementSnapshot
        if (mode.value === 'preview') {
          updated = { ...snapshot, revision: `demo-${++demoSequence}`, capabilities: { ...snapshot.capabilities, [capability]: { ...permission, enabled: !!enabled } }, last_change: { actor_id: 'LOCAL-DEMO-USER', at: new Date().toISOString(), reason: reason.trim(), audit_id: `DEMO-CHANGE-${demoSequence}` } }
          demo = updated
        } else {
          updated = parseManagement(await request('/api/v27/management/configure', active.signal, { capability, enabled, reason: reason.trim(), expected_revision: snapshot.revision, request_id: crypto.randomUUID() }))
          ensure(updated.revision !== snapshot.revision && updated.capabilities[capability].enabled === enabled && updated.last_change?.reason === reason.trim())
          for (const other of capabilityKeys.filter(item => item !== capability)) ensure(updated.capabilities[other].enabled === snapshot.capabilities[other].enabled)
        }
        if (token !== revision) return false
        data.value = updated
        feedback.value = mode.value === 'preview' ? '演示配置已更新，仅影响本地演示。' : '服务端已确认配置变更并返回审计标识。'
      } else if (action === 'release') {
        const result = mode.value === 'preview' ? { target: boundTarget!, assessment_id: 'DEMO-ASSESSMENT', assessed_at: new Date().toISOString(), verdict: 'BLOCK' as const, reasons: ['演示没有真实运行样本，不能判定通过。'], checks: [{ name: '有效运行样本', passed: false, actual: '0 次 / 0 类', required: '至少 5 次 / 3 类' }] } : parseRelease(await request('/api/v27/management/assess-release', active.signal, { target: boundTarget, expected_revision: snapshot.revision, request_id: crypto.randomUUID() }), boundTarget!)
        if (token !== revision) return false
        release.value = result; feedback.value = '评估已返回；此操作没有执行发布。'
      } else {
        const result = mode.value === 'preview' ? { target: boundTarget!, quote_id: 'DEMO-QUOTE', created_at: new Date().toISOString(), currency: 'CNY' as const, total_minor: 20000, items: [{ item_id: 'DEMO-ITEM-1', decision_id: 'DEMO-DECISION', action: '补齐缺失证据', basis: '合成示例：2 小时 × 100 元/小时，无真实计价依据', amount_minor: 20000 }] } : parseQuote(await request('/api/v27/management/quote', active.signal, { target: boundTarget, expected_revision: snapshot.revision, request_id: crypto.randomUUID() }), boundTarget!)
        if (token !== revision) return false
        quote.value = result; feedback.value = '报价已返回；未创建订单或执行整改。'
      }
      return true
    } catch (cause) {
      if (token === revision) { error.value = cause instanceof ManagementError ? cause.message : '提交结果未知，请先刷新确认，避免重复操作。'; stale.value = true }
      return false
    } finally { clearTimeout(timer); if (token === revision) submitting.value = false }
  }
  watch(mode, () => { revision++; controller?.abort(); submitting.value = false; void load() }, { immediate: true })
  watch(target, () => { revision++; controller?.abort(); submitting.value = false; clearResults(); void load() })
  onScopeDispose(() => { revision++; controller?.abort() })
  return { data, release, quote, loading, submitting, stale, error, feedback, load, mutate }
}
