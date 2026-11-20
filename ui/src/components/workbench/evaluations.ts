import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'


export const rateKeys = ['declaration_consistency', 'evidence_completeness', 'reproducibility', 'replay_consistency', 'freshness', 'mark_density', 'disposition_execution'] as const
export type RateKey = typeof rateKeys[number]
export type ReadingKey = RateKey | 'uncertainty_debt'
export interface Rate { numerator: number; denominator: number; value: number | null; status: 'VALUE' | 'NOT_APPLICABLE' }
export interface AggregateRate { value: number | null; status: 'VALUE' | 'NOT_APPLICABLE'; runs_considered: number }
export type Readings = Record<RateKey, Rate> & {
  reproducibility: Rate & { evaluable_coverage: Rate }
  uncertainty_debt: { weighted_value: number; unresolved_count: number }
}
export type AggregateReadings = Record<RateKey, AggregateRate> & {
  reproducibility: AggregateRate & { evaluable_coverage: AggregateRate }
  uncertainty_debt: { weighted_value: number; unresolved_count: number }
}
export interface SampleRun {
  run_id: string; skill_id: string; skill_version: string; status: 'closed'; completed_at: string
  valid_for_aggregation: true; whitelist_decision_count: number; decision_ids: string[]; decision_classes: string[]; readings: Readings
}
export interface VersionEvaluation {
  policy_version: string; whitelist_version: string; whitelist_classes: string[]
  aggregate: {
    schema_version: 'temisguard-b-v2.7'; skill_id: string; skill_version: string
    total_snapshot_count: number; distinct_run_count: number; valid_run_count: number; sample_run_count: number
    class_count: number; sample_run_ids: string[]; status: 'ok' | '样本不足'; aggregated_readings: AggregateReadings | null
  }
  sample_runs: SampleRun[]
}
export interface EvaluationSnapshot {
  source: 'ledger' | 'frontend_preview'; revision: string; generated_at: string | null
  comparison_enabled: boolean; versions: VersionEvaluation[]
}
export const readingDefinitions: { key: ReadingKey; name: string; formula: string; note: string }[] = [
  { key: 'declaration_consistency', name: '声明一致率', formula: '实际参数与策略声明一致的决策数 / 有声明可比对的决策数', note: '没有可比声明时为不适用。' },
  { key: 'evidence_completeness', name: '证据完整率', formula: '证据记录字段齐全的决策数 / 全部决策数', note: '字段齐全不代表证据内容真实；仍须核验证据。' },
  { key: 'reproducibility', name: '复现率', formula: '重放后判定一致的决策数 / 全部决策数', note: '缺证据计为未复现，保留在分母内；可评估覆盖率单独登记。' },
  { key: 'replay_consistency', name: '回放一致率', formula: '规则判定部分回放一致的决策数 / 规则判定部分的决策数', note: '模型输出比对结构化字段，不要求文本完全一致。' },
  { key: 'freshness', name: '时效有效率', formula: '证据与动作时间偏差不超过 5 秒的决策数 / 全部决策数', note: '以证据采集时间与动作发生时间核对。' },
  { key: 'mark_density', name: '标记密度', formula: '带至少一类标记的决策数 / 全部决策数', note: '同一决策有多个标记只计一次，上限 100%；不设达标线。' },
  { key: 'disposition_execution', name: '处置执行率', formula: '按 Gate 结果完成处置的决策数 / 应处置决策数', note: 'DENY 未执行、ASK 已应答并按结果执行均计为已处置。' },
  { key: 'uncertainty_debt', name: '不确定债务', formula: '未消解标记按原始等级加权：high 3、mid 2、low 1', note: '直接登记加权值与条目数，无分母；不换算为百分比，不设达标线。' },
]

export class EvaluationError extends Error {}
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0
const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0
const number = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0
const timestamp = (value: unknown): value is string => text(value) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(text) && new Set(value).size === value.length
const near = (left: number, right: number) => Math.abs(left - right) <= 0.0000011
function requireValue(condition: unknown): asserts condition {
  if (!condition) throw new EvaluationError('评估数据口径或样本绑定不一致，已停止展示。请刷新或联系管理员核对。')
}
function validRate(value: unknown): value is Rate {
  if (!object(value) || !count(value.numerator) || !count(value.denominator) || value.numerator > value.denominator) return false
  return value.denominator === 0
    ? value.status === 'NOT_APPLICABLE' && value.value === null
    : value.status === 'VALUE' && number(value.value) && value.value <= 1 && near(value.value, value.numerator / value.denominator)
}
function validDebt(value: unknown, integer: boolean): value is Readings['uncertainty_debt'] {
  return object(value) && number(value.weighted_value) && number(value.unresolved_count) &&
    (!integer || (count(value.weighted_value) && count(value.unresolved_count))) &&
    value.weighted_value >= value.unresolved_count - 0.0000011 && value.weighted_value <= 3 * value.unresolved_count + 0.0000011
}
function validateRun(value: unknown, version: VersionEvaluation): asserts value is SampleRun {
  requireValue(object(value) && text(value.run_id) && value.skill_id === version.aggregate.skill_id && value.skill_version === version.aggregate.skill_version &&
    value.status === 'closed' && value.valid_for_aggregation === true && timestamp(value.completed_at) &&
    count(value.whitelist_decision_count) && value.whitelist_decision_count > 0 && strings(value.decision_ids) &&
    value.whitelist_decision_count <= value.decision_ids.length && strings(value.decision_classes) && value.decision_classes.length > 0 && object(value.readings))
  for (const key of rateKeys) requireValue(validRate(value.readings[key]) && value.readings[key].denominator <= value.decision_ids.length)
  for (const key of ['evidence_completeness', 'reproducibility', 'freshness', 'mark_density']) requireValue((value.readings[key] as Rate).denominator === value.decision_ids.length)
  requireValue(object(value.readings.reproducibility) && validRate(value.readings.reproducibility.evaluable_coverage) && validDebt(value.readings.uncertainty_debt, true))
  requireValue(value.readings.reproducibility.evaluable_coverage.denominator === value.decision_ids.length)
  requireValue((value.readings.reproducibility.numerator as number) <= value.readings.reproducibility.evaluable_coverage.numerator)
}
function validateMean(value: unknown, rates: Rate[]) {
  const applicable = rates.filter(rate => rate.status === 'VALUE')
  requireValue(object(value) && value.runs_considered === applicable.length)
  requireValue(applicable.length
    ? value.status === 'VALUE' && number(value.value) && near(value.value, applicable.reduce((sum, rate) => sum + rate.value!, 0) / applicable.length)
    : value.status === 'NOT_APPLICABLE' && value.value === null)
}
export function versionKey(version: VersionEvaluation) {
  return JSON.stringify([version.aggregate.skill_id, version.aggregate.skill_version, version.policy_version, version.whitelist_version])
}
export function parseEvaluations(value: unknown): EvaluationSnapshot {
  requireValue(object(value) && value.source === 'ledger' && text(value.revision) && timestamp(value.generated_at) && typeof value.comparison_enabled === 'boolean' && Array.isArray(value.versions))
  const keys = new Set<string>()
  for (const candidate of value.versions) {
    requireValue(object(candidate) && text(candidate.policy_version) && text(candidate.whitelist_version) && strings(candidate.whitelist_classes) && candidate.whitelist_classes.length > 0 && candidate.whitelist_classes.length <= 11 && object(candidate.aggregate) && Array.isArray(candidate.sample_runs))
    const agg = candidate.aggregate
    requireValue(agg.schema_version === 'temisguard-b-v2.7' && text(agg.skill_id) && text(agg.skill_version) &&
      ['total_snapshot_count', 'distinct_run_count', 'valid_run_count', 'sample_run_count', 'class_count'].every(key => count(agg[key])) && strings(agg.sample_run_ids))
    const version = candidate as unknown as VersionEvaluation
    const a = version.aggregate
    requireValue(a.total_snapshot_count >= a.distinct_run_count && a.distinct_run_count >= a.valid_run_count &&
      a.sample_run_count === Math.min(a.valid_run_count, 20) && a.sample_run_ids.length === a.sample_run_count && candidate.sample_runs.length === a.sample_run_count && a.class_count <= 11)
    const key = versionKey(version)
    requireValue(!keys.has(key)); keys.add(key)
    const decisionIds = new Set<string>(), classes = new Set<string>()
    version.sample_runs.forEach((run, index) => {
      validateRun(run, version)
      requireValue(run.run_id === a.sample_run_ids[index])
      run.decision_ids.forEach(id => { requireValue(!decisionIds.has(id)); decisionIds.add(id) })
      const matched = run.decision_classes.filter(name => version.whitelist_classes.includes(name))
      requireValue(matched.length > 0 && matched.length <= run.whitelist_decision_count)
      matched.forEach(name => classes.add(name))
      const previous = version.sample_runs[index - 1]
      if (previous) requireValue(Date.parse(previous.completed_at) > Date.parse(run.completed_at) || (Date.parse(previous.completed_at) === Date.parse(run.completed_at) && previous.run_id < run.run_id))
    })
    requireValue(a.class_count === classes.size)
    const sufficient = a.sample_run_count >= 5 && a.class_count >= 3
    requireValue(sufficient ? a.status === 'ok' && object(a.aggregated_readings) : a.status === '样本不足' && a.aggregated_readings === null)
    if (sufficient) {
      const readings = a.aggregated_readings!
      for (const name of rateKeys) validateMean(readings[name], version.sample_runs.map(run => run.readings[name]))
      requireValue(object(readings.reproducibility))
      validateMean(readings.reproducibility.evaluable_coverage, version.sample_runs.map(run => run.readings.reproducibility.evaluable_coverage))
      requireValue(validDebt(readings.uncertainty_debt, false))
      for (const name of ['weighted_value', 'unresolved_count'] as const) requireValue(near(readings.uncertainty_debt[name], version.sample_runs.reduce((sum, run) => sum + run.readings.uncertainty_debt[name], 0) / a.sample_run_count))
    }
  }
  return value as unknown as EvaluationSnapshot
}

export function compareVersions(baseline: VersionEvaluation | undefined, candidate: VersionEvaluation | undefined): string {
  if (!baseline || !candidate) return '请选择基准版本与候选版本。'
  if (baseline.aggregate.skill_id !== candidate.aggregate.skill_id) return '不同 Skill 无法进行版本比较。'
  if (baseline.aggregate.skill_version === candidate.aggregate.skill_version) return '请选择两个不同的 Skill 版本。'
  if (baseline.policy_version !== candidate.policy_version || baseline.whitelist_version !== candidate.whitelist_version || baseline.aggregate.schema_version !== candidate.aggregate.schema_version || JSON.stringify([...baseline.whitelist_classes].sort()) !== JSON.stringify([...candidate.whitelist_classes].sort())) return '策略、白名单或读数口径不同，无法直接比较。'
  if (baseline.aggregate.status !== 'ok' || candidate.aggregate.status !== 'ok') return '至少一个版本样本不足，暂不计算版本差值。'
  return ''
}
export function formatReading(readings: AggregateReadings | null | undefined, key: ReadingKey) {
  if (!readings) return 'N/A'
  if (key === 'uncertainty_debt') return `${formatNumber(readings.uncertainty_debt.weighted_value)}`
  const rate = readings[key]
  return rate.status === 'NOT_APPLICABLE' ? '不适用' : `${formatNumber(rate.value! * 100)}%`
}
export function formatNumber(value: number) { return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }
export function formatDelta(baseline: AggregateReadings, candidate: AggregateReadings, key: ReadingKey) {
  if (key !== 'uncertainty_debt' && (baseline[key].value === null || candidate[key].value === null)) return '不适用'
  const difference = key === 'uncertainty_debt' ? candidate.uncertainty_debt.weighted_value - baseline.uncertainty_debt.weighted_value : (candidate[key].value! - baseline[key].value!) * 100
  const rounded = Math.round(difference * 100) / 100
  return `${rounded > 0 ? '+' : ''}${formatNumber(Object.is(rounded, -0) ? 0 : rounded)}${key === 'uncertainty_debt' ? ' 权重' : ' 个百分点'}`
}

export function useEvaluations(initialMode: 'preview' | 'api' = 'api') {
  const mode = ref<'preview' | 'api'>(initialMode)
  const data = shallowRef<EvaluationSnapshot | null>(null), loading = ref(false), error = ref('')
  let revision = 0, controller: AbortController | undefined
  async function load() {
    const token = ++revision
    controller?.abort(); controller = new AbortController()
    const active = controller, timer = setTimeout(() => active.abort(), 12000)
    data.value = null; error.value = ''; loading.value = true
    try {
      if (mode.value === 'preview') { data.value = { source: 'frontend_preview', revision: 'preview', generated_at: null, comparison_enabled: false, versions: [] }; return }
      const response = await fetch('/api/v27/evaluations', { signal: active.signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      if (!response.ok) throw new EvaluationError(({ 401: '登录已失效，请重新登录后重试。', 403: '当前账号无权查看版本评估，请确认授权范围。', 404: '评估服务尚未提供此接口。' } as Record<number, string>)[response.status] ?? '评估服务暂时不可用，请稍后重试。')
      const result = parseEvaluations(await response.json())
      if (token === revision) data.value = result
    } catch (cause) {
      if (token === revision) error.value = cause instanceof EvaluationError ? cause.message : '读取评估失败或超时，请检查连接后重试。'
    } finally { clearTimeout(timer); if (token === revision) loading.value = false }
  }
  watch(mode, load, { immediate: true })
  onScopeDispose(() => { revision++; controller?.abort() })
  return { mode, data, loading, error, load, versions: computed(() => data.value?.versions ?? []) }
}
