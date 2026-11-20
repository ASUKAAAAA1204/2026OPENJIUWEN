import { onScopeDispose, ref, shallowRef, watch, type Ref } from 'vue'
import { previewDecisions, type DecisionRecord } from './audit'
import type { RunPreview } from './model'

export interface AuditSnapshot {
  source: 'ledger' | 'frontend_preview'
  run_id: string
  state: 'open' | 'closed' | 'degraded' | 'preview'
  segment: number
  parent_run_id: string | null
  child_run_ids: string[]
  decisions: DecisionRecord[]
  chain: { status: 'verified' | 'invalid' | 'unverified'; reason: string }
}
export type AuditMode = 'preview' | 'api'
export type ResourceState = 'loading' | 'ready' | 'empty' | 'error' | 'forbidden' | 'unauthorized' | 'not-found'
export class AuditSourceError extends Error {
  constructor(public state: ResourceState, message: string) { super(message) }
}
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string')
const nullableString = (value: unknown) => value === null || typeof value === 'string'
const member = (value: unknown, values: string[]) => typeof value === 'string' && values.includes(value)
function validDecision(value: unknown, runId: string): value is DecisionRecord {
  if (!object(value)) return false
  const textFields = ['decision_id', 'class', 'tool_name', 'args_digest', 'whitelist_version', 'policy_version', 'gate_reason', 'status']
  if (!textFields.every(key => typeof value[key] === 'string') || value.run_id !== runId || !Number.isInteger(value.segment) || (value.segment as number) < 1) return false
  if (!member(value.gate, ['ALLOW', 'ASK', 'DENY', 'REWRITE', 'NONE']) || !member(value.exec_policy, ['normal', 'degrade'])) return false
  if (![value.args_after, value.rule_snapshot_ref, value.evidence_ref].every(nullableString)) return false
  if (value.gate_latency_ms !== null && (typeof value.gate_latency_ms !== 'number' || !Number.isFinite(value.gate_latency_ms) || value.gate_latency_ms < 0)) return false
  if (value.evidence === null) return true
  if (!object(value.evidence)) return false
  return ['evidence_id', 'input_digest', 'output_digest', 'model_id', 'model_params', 'verdict'].every(key => typeof (value.evidence as Record<string, unknown>)[key] === 'string') &&
    (value.evidence.ts === null || (typeof value.evidence.ts === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value.evidence.ts) && Number.isFinite(Date.parse(value.evidence.ts)))) &&
    (value.evidence.content_hash === null || (typeof value.evidence.content_hash === 'string' && /^[a-f0-9]{64}$/.test(value.evidence.content_hash))) &&
    typeof value.evidence.evidence_id === 'string' && !!value.evidence.evidence_id.trim() && value.evidence_ref === value.evidence.evidence_id
}
export function parseAuditSnapshot(value: unknown, runId: string): AuditSnapshot {
  if (!object(value) || value.source !== 'ledger' || value.run_id !== runId || !member(value.state, ['open', 'closed', 'degraded']) ||
    !Number.isInteger(value.segment) || (value.segment as number) < 1 || !nullableString(value.parent_run_id) || !strings(value.child_run_ids) ||
    value.parent_run_id === runId || (typeof value.parent_run_id === 'string' && !value.parent_run_id.trim()) || value.child_run_ids.some(id => !id.trim() || id === runId || id === value.parent_run_id) || new Set(value.child_run_ids).size !== value.child_run_ids.length ||
    !Array.isArray(value.decisions) || !value.decisions.every(item => validDecision(item, runId)) ||
    new Set(value.decisions.map(item => item.decision_id)).size !== value.decisions.length ||
    !object(value.chain) || !member(value.chain.status, ['verified', 'invalid', 'unverified']) || typeof value.chain.reason !== 'string') {
    throw new AuditSourceError('error', '收到的数据不符合运行账本契约，已停止展示，请重试或联系管理员。')
  }
  return value as unknown as AuditSnapshot
}

/** 约定接口待后端实现；错误显式展示，绝不回退为演示成功。 */
export async function loadAudit(run: RunPreview, mode: AuditMode, signal: AbortSignal): Promise<AuditSnapshot> {
  if (mode === 'preview') return {
    source: 'frontend_preview', run_id: run.id, state: 'preview', segment: 1,
    parent_run_id: null, child_run_ids: [], decisions: previewDecisions(run),
    chain: { status: 'unverified', reason: '预览未写入账本，无法校验。' },
  }
  const response = await fetch(`/api/v27/runs/${encodeURIComponent(run.id)}/audit`, { signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
  if (!response.ok) {
    const errors: Record<number, [ResourceState, string]> = {
      401: ['unauthorized', '登录已失效，请重新登录后重试。'],
      403: ['forbidden', '当前账号无权查看此运行，请联系管理员确认授权范围。'],
      404: ['not-found', '未找到运行，或账本服务尚未提供此接口。'],
    }
    const [state, message] = errors[response.status] ?? ['error', '账本服务暂时不可用，请稍后重试。']
    throw new AuditSourceError(state, message)
  }
  return parseAuditSnapshot(await response.json(), run.id)
}

export function useAuditSource(run: Ref<RunPreview>, mode: Ref<AuditMode>) {
  const data = shallowRef<AuditSnapshot | null>(null)
  const state = ref<ResourceState>('loading')
  const error = ref('')
  let controller: AbortController | undefined
  let revision = 0
  async function reload() {
    const current = ++revision
    controller?.abort()
    controller = new AbortController()
    const active = controller
    data.value = null
    error.value = ''
    state.value = 'loading'
    const timeout = setTimeout(() => active.abort(), 12000)
    try {
      const result = await loadAudit(run.value, mode.value, active.signal)
      if (current !== revision) return
      data.value = result
      state.value = result.decisions.length ? 'ready' : 'empty'
    } catch (cause) {
      if (current !== revision) return
      state.value = cause instanceof AuditSourceError ? cause.state : 'error'
      error.value = cause instanceof AuditSourceError ? cause.message : active.signal.aborted ? '请求超时，请检查本机账本服务后重试。' : '无法读取账本，请检查连接后重试。'
    } finally { clearTimeout(timeout) }
  }
  watch([() => run.value.id, mode], reload, { immediate: true })
  onScopeDispose(() => { revision++; controller?.abort() })
  return { data, state, error, reload }
}
