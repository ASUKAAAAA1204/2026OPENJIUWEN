import { onScopeDispose, ref, shallowRef, watch } from 'vue'
import { writeSessionHeaders } from './writeSession'

export type MarkType = 'NO_EVIDENCE' | 'SOURCE_CONFLICT' | 'SPEC_MISMATCH'
export type Severity = 'high' | 'mid' | 'low'
export const debtWeights = { high: 3, mid: 2, low: 1 }
export const remedies = {
  NO_EVIDENCE: { label: '补充结果证据', method: 'supplement_evidence' },
  SOURCE_CONFLICT: { label: '统一来源并提交核验依据', method: 'unify_sources' },
  SPEC_MISMATCH: { label: '修正参数并重新过闸', method: 'correct_params_and_regate' },
} as const
export const markLabels = { NO_EVIDENCE: '缺少证据', SOURCE_CONFLICT: '来源冲突', SPEC_MISMATCH: '口径不一致' }
export interface DebtItem {
  debt_id: string
  run_id: string
  decision_id: string
  mark_type: MarkType
  original_severity: Severity
  display_severity: Severity
  weight: number
  status: 'open' | 'resolved'
  cause: string | null
  provenance: string[]
  opened_seq: number
  resolved: null | { seq: number; method: string; evidence_ref: { evidence_id: string; content_hash: string }; regate_success: boolean | null }
}
export interface DebtSnapshot {
  source: 'ledger' | 'frontend_preview'
  items: DebtItem[]
  can_resolve: boolean
  runs: DebtRun[] | null
}
export interface DebtRun {
  run_id: string
  parent_run_id: string | null
  review_threshold: number
  review_status: 'queued' | 'not_queued'
  final_reading: { total: number; count: number } | null
}
export interface ResolutionInput {
  evidence_id: string
  content_hash: string
  regate_decision_id: string
  confirmed: boolean
}
export class DebtError extends Error { constructor(message: string) { super(message) } }
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const hash = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`
  if (object(value)) return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`
  return JSON.stringify(value)
}
export function normalizeDebtItems(value: unknown): DebtItem[] {
  if (!Array.isArray(value)) throw new DebtError('债务列表格式不正确。')
  const unique = new Map<string, DebtItem>()
  const ids = new Map<string, string>()
  for (const item of value) {
    if (!object(item) || !['debt_id', 'run_id', 'decision_id'].every(key => typeof item[key] === 'string' && !!item[key]) ||
      !Object.prototype.hasOwnProperty.call(remedies, String(item.mark_type)) || !Object.prototype.hasOwnProperty.call(debtWeights, String(item.original_severity)) || !Object.prototype.hasOwnProperty.call(debtWeights, String(item.display_severity)) ||
      item.weight !== debtWeights[item.original_severity as Severity] || !['open', 'resolved'].includes(String(item.status)) ||
      (item.cause !== null && typeof item.cause !== 'string') || !Array.isArray(item.provenance) || !item.provenance.every(v => typeof v === 'string') ||
      !Number.isInteger(item.opened_seq) || (item.opened_seq as number) < 0) throw new DebtError('债务字段或原始权重不一致，已停止展示。')
    if (debtWeights[item.display_severity as Severity] > debtWeights[item.original_severity as Severity] || (item.cause === 'UNKNOWN_DEPENDENCY' && item.display_severity !== item.original_severity)) throw new DebtError('传播呈现不符合原始等级保留规则。')
    if (item.status === 'open' && item.resolved !== null) throw new DebtError('未消解条目含有冲突的消解结果。')
    if (item.status === 'resolved') {
      const resolution = item.resolved
      if (!object(resolution) || !Number.isInteger(resolution.seq) || (resolution.seq as number) <= (item.opened_seq as number) ||
        resolution.method !== remedies[item.mark_type as MarkType].method || !object(resolution.evidence_ref) || typeof resolution.evidence_ref.evidence_id !== 'string' || !resolution.evidence_ref.evidence_id || !hash(resolution.evidence_ref.content_hash) ||
        (item.mark_type === 'SPEC_MISMATCH' ? resolution.regate_success !== true : resolution.regate_success !== null)) throw new DebtError('消解依据不完整或方法与标记不匹配。')
    }
    const key = JSON.stringify([item.run_id, item.decision_id, item.mark_type])
    const existing = unique.get(key)
    if ((existing && stable(existing) !== stable(item)) || (ids.has(String(item.debt_id)) && ids.get(String(item.debt_id)) !== key)) throw new DebtError('重复债务内容冲突，不能继续汇总。')
    unique.set(key, item as unknown as DebtItem); ids.set(String(item.debt_id), key)
  }
  return [...unique.values()]
}
export function debtSummary(items: DebtItem[]) {
  const unique = normalizeDebtItems(items)
  return { total: unique.filter(item => item.status === 'open').reduce((sum, item) => sum + item.weight, 0), open: unique.filter(item => item.status === 'open').length, retained: unique.length }
}
export function parseDebtRuns(value: unknown, items: DebtItem[]): DebtRun[] | null {
  if (value === undefined || value === null) return null
  if (!Array.isArray(value)) throw new DebtError('运行债务读数格式不正确。')
  const runs = new Map<string, DebtRun>()
  for (const run of value) {
    if (!object(run) || typeof run.run_id !== 'string' || !run.run_id || runs.has(run.run_id) ||
      !(run.parent_run_id === null || (typeof run.parent_run_id === 'string' && !!run.parent_run_id)) ||
      typeof run.review_threshold !== 'number' || !Number.isFinite(run.review_threshold) || run.review_threshold < 0 ||
      !['queued', 'not_queued'].includes(String(run.review_status)) ||
      !(run.final_reading === null || (object(run.final_reading) &&
        Number.isSafeInteger(run.final_reading.total) && (run.final_reading.total as number) >= 0 &&
        Number.isSafeInteger(run.final_reading.count) && (run.final_reading.count as number) >= 0 &&
        (run.final_reading.total as number) >= (run.final_reading.count as number) &&
        (run.final_reading.total as number) <= (run.final_reading.count as number) * 3))) {
      throw new DebtError('运行关系、复核阈值或结束读数无效，已停止汇总。')
    }
    runs.set(run.run_id, run as unknown as DebtRun)
  }
  for (const run of runs.values()) {
    const seen = new Set([run.run_id])
    let parent = run.parent_run_id
    while (parent !== null) {
      if (!runs.has(parent) || seen.has(parent) || seen.size > 3) throw new DebtError('运行父子关系缺失、循环或超过委派深度，已停止汇总。')
      seen.add(parent); parent = runs.get(parent)!.parent_run_id
    }
  }
  if (items.some(item => !runs.has(item.run_id))) throw new DebtError('债务所有权缺少对应运行，已停止汇总。')
  return [...runs.values()]
}
export function runDebtSummary(items: DebtItem[], runs: DebtRun[], runId: string) {
  const included = new Set([runId])
  // Expand ownership once per run; the list itself never contains parent copies of child debts.
  for (let depth = 0; depth < 3; depth++) {
    for (const run of runs) if (run.parent_run_id !== null && included.has(run.parent_run_id)) included.add(run.run_id)
  }
  const own = debtSummary(items.filter(item => item.run_id === runId))
  const children = debtSummary(items.filter(item => item.run_id !== runId && included.has(item.run_id)))
  return { own: own.total, children: children.total, total: own.total + children.total, count: own.open + children.open }
}
export function validateResolution(item: DebtItem, input: ResolutionInput): string | null {
  if (item.status !== 'open') return '此条债务已消解，请刷新核对。'
  if (!input.evidence_id.trim()) return '请填写内容寻址证据标识。'
  if (!hash(input.content_hash)) return '证据指纹应为64位小写十六进制SHA-256。'
  if (item.mark_type === 'SPEC_MISMATCH' && !input.regate_decision_id.trim()) return '参数修正必须关联重新过闸的决策记录。'
  if (!input.confirmed) return '请确认已核对消解方法与证据范围。'
  return null
}
export function createDebtDemo(): DebtSnapshot {
  const items = (Object.keys(remedies) as MarkType[]).map((type, index): DebtItem => ({
    debt_id: `DEBT-DEMO-${index + 1}`, run_id: index === 2 ? 'RUN-DEBT-CHILD' : 'RUN-DEBT-DEMO', decision_id: `DECISION-DEBT-${index + 1}`,
    mark_type: type, original_severity: (['high', 'mid', 'low'] as Severity[])[index], display_severity: index === 0 ? 'mid' : (['high', 'mid', 'low'] as Severity[])[index],
    weight: 3 - index, status: 'open', cause: index === 0 ? '上游输出缺少结果快照' : index === 1 ? 'UNKNOWN_DEPENDENCY' : '参数单位与策略声明不一致',
    provenance: index === 0 ? ['DECISION-UPSTREAM-DEMO'] : [], opened_seq: index, resolved: null,
  }))
  return { source: 'frontend_preview', can_resolve: true, items, runs: [
    { run_id: 'RUN-DEBT-DEMO', parent_run_id: null, review_threshold: 5, review_status: 'queued', final_reading: { total: 5, count: 2 } },
    { run_id: 'RUN-DEBT-CHILD', parent_run_id: 'RUN-DEBT-DEMO', review_threshold: 3, review_status: 'not_queued', final_reading: null },
  ] }
}
function parseSnapshot(value: unknown): DebtSnapshot {
  if (!object(value) || value.source !== 'ledger' || typeof value.can_resolve !== 'boolean') throw new DebtError('债务服务返回了无效契约。')
  const items = normalizeDebtItems(value.items)
  return { source: 'ledger', can_resolve: value.can_resolve, items, runs: parseDebtRuns(value.runs, items) }
}
async function request(path: string, signal: AbortSignal, payload?: unknown): Promise<unknown> {
  let sessionHeaders = {}
  if (payload) { try { sessionHeaders = await writeSessionHeaders(signal) } catch (cause) { if (signal.aborted) throw cause; throw new DebtError(cause instanceof Error ? cause.message : '无法核验会话，本次操作尚未提交。') } }
  const result = await fetch(path, { method: payload ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', signal, headers: { Accept: 'application/json', ...sessionHeaders, ...(payload ? { 'Content-Type': 'application/json' } : {}), ...(object(payload) && typeof payload.request_id === 'string' ? { 'Idempotency-Key': payload.request_id } : {}) }, ...(payload ? { body: JSON.stringify(payload) } : {}) })
  if (!result.ok) {
    const messages: Record<number, string> = { 401: '登录已失效，请重新登录。', 403: '当前账号没有消解或读取权限。', 404: '债务不存在或接口尚未提供。', 409: '债务状态或证据已变化，请刷新后重新核对。', 422: '证据验证或重新过闸未通过，债务仍保留。' }
    throw new DebtError(messages[result.status] ?? '债务服务暂时不可用，请刷新重试。')
  }
  return result.json()
}
export function useDebts(initialMode: 'preview' | 'api' = 'api') {
  const mode = ref<'preview' | 'api'>(initialMode)
  const data = shallowRef<DebtSnapshot | null>(null)
  const loading = ref(false), submitting = ref(false), stale = ref(false), error = ref('')
  let demo = createDebtDemo(), revision = 0, controller: AbortController | undefined
  async function load() {
    const token = ++revision
    controller?.abort(); controller = new AbortController()
    const active = controller
    data.value = null; loading.value = true; error.value = ''; stale.value = false
    const timer = setTimeout(() => active.abort(), 12000)
    try {
      const result = mode.value === 'preview' ? demo : parseSnapshot(await request('/api/v27/debts', active.signal))
      if (token === revision) data.value = result
    } catch (cause) { if (token === revision) error.value = cause instanceof DebtError ? cause.message : '债务读取失败或超时，请重试。' }
    finally { clearTimeout(timer); if (token === revision) loading.value = false }
  }
  async function resolve(item: DebtItem, input: ResolutionInput): Promise<boolean> {
    if (submitting.value) return false
    error.value = ''
    const live = data.value?.items.find(v => v.debt_id === item.debt_id)
    if (stale.value || !live || live.status !== 'open' || !data.value?.can_resolve) { error.value = '状态已变化或无消解权限，请刷新核对。'; return false }
    const invalid = validateResolution(item, input)
    if (invalid) { error.value = invalid; return false }
    submitting.value = true
    const token = revision, active = new AbortController()
    controller = active
    const timer = setTimeout(() => active.abort(), 12000)
    try {
      let updated: DebtItem
      if (mode.value === 'preview') {
        const maxSeq = Math.max(...demo.items.map(v => v.resolved?.seq ?? v.opened_seq))
        updated = { ...item, status: 'resolved', resolved: { seq: maxSeq + 1, method: remedies[item.mark_type].method, evidence_ref: { evidence_id: input.evidence_id.trim(), content_hash: input.content_hash }, regate_success: item.mark_type === 'SPEC_MISMATCH' ? true : null } }
      } else {
        const result = await request(`/api/v27/debts/${encodeURIComponent(item.debt_id)}/resolve`, active.signal, { method: remedies[item.mark_type].method, evidence_ref: { evidence_id: input.evidence_id.trim(), content_hash: input.content_hash }, regate_decision_id: item.mark_type === 'SPEC_MISMATCH' ? input.regate_decision_id.trim() : null, expected_opened_seq: item.opened_seq, request_id: crypto.randomUUID() })
        updated = normalizeDebtItems([result])[0]
        if (updated.status !== 'resolved' || stable({ ...updated, status: 'open', resolved: null }) !== stable(item) || updated.resolved?.evidence_ref.evidence_id !== input.evidence_id.trim() || updated.resolved?.evidence_ref.content_hash !== input.content_hash) throw new DebtError('服务结果改变了原始条目或证据引用，请刷新核对。')
      }
      if (token !== revision || !data.value) return false
      const result = { ...data.value, items: data.value.items.map(v => v.debt_id === updated.debt_id ? updated : v) }
      data.value = result
      if (mode.value === 'preview') demo = result
      return true
    } catch (cause) { if (token === revision) { error.value = cause instanceof DebtError ? cause.message : '提交结果未知，请刷新确认，不要重复提交。'; stale.value = true }; return false }
    finally { clearTimeout(timer); submitting.value = false }
  }
  watch(mode, load, { immediate: true })
  onScopeDispose(() => { revision++; controller?.abort() })
  return { mode, data, loading, submitting, stale, error, load, resolve }
}
