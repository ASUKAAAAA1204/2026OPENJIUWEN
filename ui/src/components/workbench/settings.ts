import { onScopeDispose, ref, shallowRef, watch } from 'vue'

export const roles = ['平台管理员', '值班人员', '开发者', '合规审计', '平台运营'] as const
export interface PolicyRule { decision_class: string; gate: 'ALLOW' | 'ASK' | 'DENY' | 'REWRITE'; exec_policy: 'normal' | 'degrade'; risk: 'high' | 'mid' | 'low'; ask_timeout_seconds: number | null; ask_timeout_policy: 'ALLOW' | 'DENY' | null }
export interface PolicySnapshot { version: string; content_hash: string; rules: PolicyRule[]; history: { version: string; actor_id: string; at: string; reason: string }[] }
export interface SettingsSnapshot {
  source: 'ledger' | 'frontend_preview'; revision: string
  identity: { user_id: string; display_name: string; role: typeof roles[number]; scope: string } | null
  policy: PolicySnapshot | null
  whitelist: { version: string; classes: string[]; tools: { name: string; capability: 'read_only' | 'mixed' | 'write'; workspace: string }[] } | null
  frameworks: { name: 'openJiuwen' | 'LangGraph' | 'OpenAI Agents SDK'; version: string | null; status: 'verified' | 'unverified' | 'unavailable'; checked_at: string | null; reason: string }[]
}
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
const time = (value: unknown) => text(value) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))
function ensure(value: unknown): asserts value { if (!value) throw new Error('配置响应不符合版本或权限契约，已停止展示。') }
export function parseSettings(value: unknown): SettingsSnapshot {
  ensure(object(value) && value.source === 'ledger' && text(value.revision))
  const identity = value.identity
  ensure(identity === null || (object(identity) && text(identity.user_id) && text(identity.display_name) && roles.includes(identity.role as typeof roles[number]) && text(identity.scope)))
  const policy = value.policy
  if (policy !== null) parsePolicy(policy)
  const whitelist = value.whitelist
  if (whitelist !== null) {
    ensure(object(whitelist) && text(whitelist.version) && Array.isArray(whitelist.classes) && whitelist.classes.every(text) && new Set(whitelist.classes).size === whitelist.classes.length && whitelist.classes.length <= 11 && Array.isArray(whitelist.tools))
    for (const tool of whitelist.tools) ensure(object(tool) && text(tool.name) && ['read_only', 'mixed', 'write'].includes(String(tool.capability)) && text(tool.workspace))
    ensure(new Set(whitelist.tools.map(tool => tool.name)).size === whitelist.tools.length)
  }
  ensure(Array.isArray(value.frameworks) && value.frameworks.length === 3)
  const names = new Set<string>()
  for (const framework of value.frameworks) {
    ensure(object(framework) && ['openJiuwen', 'LangGraph', 'OpenAI Agents SDK'].includes(String(framework.name)) && ['verified', 'unverified', 'unavailable'].includes(String(framework.status)) && text(framework.reason) && (framework.version === null || text(framework.version)) && (framework.checked_at === null || time(framework.checked_at)))
    ensure(!names.has(String(framework.name))); names.add(String(framework.name))
    if (framework.status === 'verified') ensure(text(framework.version) && time(framework.checked_at) && (framework.name !== 'openJiuwen' || framework.version === '0.1.20.post1'))
  }
  return value as unknown as SettingsSnapshot
}
export function parsePolicy(policy: unknown): PolicySnapshot {
    ensure(object(policy) && text(policy.version) && typeof policy.content_hash === 'string' && /^[a-f0-9]{64}$/.test(policy.content_hash) && Array.isArray(policy.rules) && Array.isArray(policy.history))
    for (const rule of policy.rules) {
      ensure(object(rule) && text(rule.decision_class) && ['ALLOW', 'ASK', 'DENY', 'REWRITE'].includes(String(rule.gate)) && ['normal', 'degrade'].includes(String(rule.exec_policy)) && ['high', 'mid', 'low'].includes(String(rule.risk)))
      ensure(rule.gate === 'ASK' ? typeof rule.ask_timeout_seconds === 'number' && Number.isSafeInteger(rule.ask_timeout_seconds) && rule.ask_timeout_seconds > 0 && ['ALLOW', 'DENY'].includes(String(rule.ask_timeout_policy)) && (rule.risk === 'low' || rule.ask_timeout_policy === 'DENY') : rule.ask_timeout_seconds === null && rule.ask_timeout_policy === null)
    }
    ensure(new Set(policy.rules.map(rule => rule.decision_class)).size === policy.rules.length)
    for (const item of policy.history) ensure(object(item) && text(item.version) && text(item.actor_id) && time(item.at) && text(item.reason))
  return policy as unknown as PolicySnapshot
}
export function settingsPreview(): SettingsSnapshot {
  return { source: 'frontend_preview', revision: 'preview', identity: null, policy: null, whitelist: null, frameworks: ['openJiuwen', 'LangGraph', 'OpenAI Agents SDK'].map(name => ({ name: name as SettingsSnapshot['frameworks'][number]['name'], version: null, status: 'unverified', checked_at: null, reason: name === 'openJiuwen' ? '目标锁定 0.1.20.post1，尚未读取真实接入验证。' : '尚未读取真实接入验证。' })) }
}
export function useSettings() {
  const mode = ref<'preview' | 'api'>('api')
  const data = shallowRef<SettingsSnapshot | null>(null), loading = ref(false), error = ref('')
  let revision = 0, controller: AbortController | undefined
  async function load() {
    const token = ++revision
    controller?.abort(); controller = new AbortController()
    const active = controller, timer = setTimeout(() => active.abort(), 12000)
    loading.value = true; data.value = null; error.value = ''
    try {
      const response = mode.value === 'preview' ? null : await fetch('/api/v27/settings', { signal: active.signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      if (response && !response.ok) throw new Error(({ 401: '登录已失效，请通过本机登录入口重新认证。', 403: '当前账号无权查看配置，请联系管理员确认范围。', 404: '配置服务尚未提供此接口。' } as Record<number, string>)[response.status] ?? '配置服务暂时不可用，请重试。')
      const result = response ? parseSettings(await response.json()) : settingsPreview()
      if (token === revision) data.value = result
    } catch (cause) { if (token === revision) error.value = active.signal.aborted ? '读取超时，请检查服务后重试。' : cause instanceof Error ? cause.message : '读取配置失败，请重试。' }
    finally { clearTimeout(timer); if (token === revision) loading.value = false }
  }
  watch(mode, load, { immediate: true })
  onScopeDispose(() => { revision++; controller?.abort() })
  return { mode, data, loading, error, load }
}
