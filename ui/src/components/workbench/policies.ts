import { parsePolicy, type PolicyRule, type PolicySnapshot } from './settings'
import { writeSessionHeaders } from './writeSession'
export interface PublishedPolicy {
  version: string; content_hash: string; description: string; published_at: string; published_by: string; eligible: boolean; reason: string
  configuration: { rules: PolicyRule[]; [key: string]: unknown }
}
export interface PolicyCatalog {
  source: 'ledger'; revision: string; can_switch: boolean
  active: { version: string; content_hash: string } | null; versions: PublishedPolicy[]
}
export interface PolicyChange { field: string; before: string; after: string }
export interface PolicySwitchReceipt {
  source: 'ledger'; request_id: string; catalog: PolicyCatalog
  audit: { audit_id: string; actor_id: string; at: string; reason: string; from_version: string | null; from_hash: string | null; to_version: string; to_hash: string }
}
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
const time = (value: unknown) => text(value) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))
const ensure: (condition: unknown) => asserts condition = condition => { if (!condition) throw new Error('策略版本响应不符合只读快照或切换绑定约定，请刷新核对。') }
/** Canonical comparison only; this does not verify the server-declared content hash. */
export function policyValue(value: unknown): string {
  if (value === undefined) return 'undefined'
  if (Array.isArray(value)) return `[${value.map(policyValue).join(',')}]`
  if (object(value)) return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${policyValue(value[key])}`).join(',')}}`
  return JSON.stringify(value) ?? 'null'
}
const ruleProjection = (rules: PolicyRule[]) => rules.map(rule => ({ decision_class: rule.decision_class, gate: rule.gate, exec_policy: rule.exec_policy, risk: rule.risk, ask_timeout_seconds: rule.ask_timeout_seconds, ask_timeout_policy: rule.ask_timeout_policy })).sort((a, b) => a.decision_class.localeCompare(b.decision_class))
export function parsePolicyCatalog(value: unknown): PolicyCatalog {
  ensure(object(value) && value.source === 'ledger' && text(value.revision) && typeof value.can_switch === 'boolean' && Array.isArray(value.versions))
  const ids = new Set<string>()
  for (const item of value.versions) {
    ensure(object(item) && text(item.version) && !ids.has(item.version) && text(item.description) && time(item.published_at) && text(item.published_by) && typeof item.eligible === 'boolean' && text(item.reason) && object(item.configuration))
    parsePolicy({ version: item.version, content_hash: item.content_hash, rules: item.configuration.rules, history: [] })
    ids.add(item.version)
  }
  ensure(value.active === null || (object(value.active) && value.versions.some(item => item.version === (value.active as Record<string, unknown>).version && item.content_hash === (value.active as Record<string, unknown>).content_hash)))
  return value as unknown as PolicyCatalog
}
export function matchesPolicySettings(catalog: PolicyCatalog, policy: PolicySnapshot | null): boolean {
  if (!policy || !catalog.active) return policy === null && catalog.active === null
  const active = catalog.versions.find(item => item.version === catalog.active!.version)
  return !!active && active.version === policy.version && active.content_hash === policy.content_hash && policyValue(ruleProjection(active.configuration.rules)) === policyValue(ruleProjection(policy.rules))
}
export function policyChanges(before: PublishedPolicy | null, after: PublishedPolicy): PolicyChange[] {
  const oldRules = before?.configuration.rules ?? [], newRules = after.configuration.rules
  const classes = [...new Set([...oldRules, ...newRules].map(rule => rule.decision_class))].sort()
  const changes: PolicyChange[] = []
  for (const name of classes) {
    const oldRule = oldRules.find(item => item.decision_class === name), newRule = newRules.find(item => item.decision_class === name)
    if (!oldRule || !newRule) {
      changes.push({ field: `决策类 ${name}`, before: oldRule ? JSON.stringify(oldRule, null, 2) : '未配置 → 默认 DENY', after: newRule ? JSON.stringify(newRule, null, 2) : '规则移除 → 默认 DENY' })
      continue
    }
    const labels: Record<string, string> = { gate: 'Gate', exec_policy: 'Exec', risk: '风险', ask_timeout_seconds: '等待秒数', ask_timeout_policy: '超时处置' }
    const left = oldRule as unknown as Record<string, unknown>, right = newRule as unknown as Record<string, unknown>
    for (const key of [...new Set([...Object.keys(left), ...Object.keys(right)])].sort()) {
      const display = (value: unknown, missing: string) => value === undefined ? missing : value === null && key.startsWith('ask_timeout_') ? '不适用（非 ASK）' : typeof value === 'string' ? value : JSON.stringify(value, null, 2)
      if (policyValue(left[key]) !== policyValue(right[key])) changes.push({ field: `${name} · ${labels[key] ?? key}`, before: display(left[key], '未配置'), after: display(right[key], '已移除') })
    }
  }
  for (const key of [...new Set([...Object.keys(before?.configuration ?? {}), ...Object.keys(after.configuration)])].filter(key => key !== 'rules').sort()) {
    const left = before?.configuration[key], right = after.configuration[key]
    const label = ({ debt_review_threshold: '债务复核阈值', redaction_version: '脱敏规则版本' } as Record<string, string>)[key] ?? key
    if (policyValue(left) !== policyValue(right)) changes.push({ field: `配置项 ${label}`, before: left === undefined ? '未配置' : JSON.stringify(left, null, 2), after: right === undefined ? '已移除' : JSON.stringify(right, null, 2) })
  }
  return changes
}
export function parsePolicySwitch(value: unknown, previous: PolicyCatalog, target: PublishedPolicy, requestId: string, reason: string): PolicySwitchReceipt {
  ensure(text(requestId) && text(reason) && previous.versions.some(item => item.version === target.version && item.content_hash === target.content_hash && item.eligible && policyValue(item.configuration) === policyValue(target.configuration)))
  ensure(object(value) && value.source === 'ledger' && value.request_id === requestId && object(value.audit))
  const catalog = parsePolicyCatalog(value.catalog), audit = value.audit
  ensure(catalog.revision !== previous.revision && catalog.active?.version === target.version && catalog.active.content_hash === target.content_hash && previous.can_switch && target.eligible && target.version !== previous.active?.version)
  ensure(text(audit.audit_id) && text(audit.actor_id) && time(audit.at) && audit.reason === reason && audit.from_version === (previous.active?.version ?? null) && audit.from_hash === (previous.active?.content_hash ?? null) && audit.to_version === target.version && audit.to_hash === target.content_hash)
  // Published versions are immutable across a pointer switch, including the old active version.
  ensure(previous.versions.every(old => { const next = catalog.versions.find(item => item.version === old.version); return next && next.content_hash === old.content_hash && next.description === old.description && next.published_at === old.published_at && next.published_by === old.published_by && policyValue(next.configuration) === policyValue(old.configuration) }))
  return value as unknown as PolicySwitchReceipt
}
export async function policyRequest(signal: AbortSignal, body?: Record<string, unknown>): Promise<unknown> {
  const sessionHeaders = body ? await writeSessionHeaders(signal) : {}
  let response: Response
  try { response = await fetch(body ? '/api/v27/policies/activate' : '/api/v27/policies', { signal, credentials: 'same-origin', cache: 'no-store', method: body ? 'POST' : 'GET', headers: { Accept: 'application/json', ...sessionHeaders, ...(body ? { 'Content-Type': 'application/json', 'Idempotency-Key': String(body.request_id) } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}) }) }
  catch { throw new Error(body ? '切换连接中断，结果未知。请刷新配置核对当前生效版本，勿重复提交。' : '无法连接策略版本服务，请重试。') }
  if (!response.ok) throw new Error(({ 401: '登录已失效，请登录后重新读取配置。', 403: '当前账号没有策略版本读取或切换权限。', 404: '策略版本接口尚不可用。', 409: '生效版本或候选配置已变化，请刷新配置重新核对。', 422: '候选版本不满足启用条件，请刷新查看原因。' } as Record<number, string>)[response.status] ?? '策略服务暂时不可用，请刷新核对当前版本。')
  try { return await response.json() } catch { throw new Error('策略响应不可解析，请刷新配置核对，当前不能确认操作结果。') }
}
