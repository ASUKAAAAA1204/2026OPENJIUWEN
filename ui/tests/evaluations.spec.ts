import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick } from 'vue'
import { compareVersions, formatDelta, formatReading, parseEvaluations, useEvaluations } from '../src/components/workbench/evaluations'
import { evaluationFixture } from './fixtures/evaluations'
afterEach(() => vi.unstubAllGlobals())
describe('版本读数与样本约束', () => {
  it('验证等权均值而非池化；零值与不适用分别呈现', () => {
    const data = parseEvaluations(evaluationFixture())
    expect(formatReading(data.versions[0].aggregate.aggregated_readings, 'evidence_completeness')).toBe('80%')
    expect(formatReading(data.versions[1].aggregate.aggregated_readings, 'evidence_completeness')).toBe('0%')
    expect(formatReading(data.versions[0].aggregate.aggregated_readings, 'replay_consistency')).toBe('不适用')
    const wrong = evaluationFixture()
    wrong.versions[0].aggregate.aggregated_readings!.evidence_completeness.value = 40 / 42
    expect(() => parseEvaluations(wrong)).toThrow('口径')
  })
  it('不足五次或三类不能返回聚合值', () => {
    const data = evaluationFixture()
    data.versions[2].aggregate.aggregated_readings = data.versions[0].aggregate.aggregated_readings
    expect(() => parseEvaluations(data)).toThrow()
    const classes = evaluationFixture()
    classes.versions[0].aggregate.class_count = 2
    expect(() => parseEvaluations(classes)).toThrow()
  })
  it('不适用运行排除出等权均值，样本窗口不超过20次', () => {
    const data = evaluationFixture()
    data.versions[0].sample_runs[0].readings.replay_consistency = { numerator: 1, denominator: 1, value: 1, status: 'VALUE' }
    data.versions[0].aggregate.aggregated_readings!.replay_consistency = { value: 1, status: 'VALUE', runs_considered: 1 }
    expect(parseEvaluations(data).versions[0].aggregate.aggregated_readings!.replay_consistency.runs_considered).toBe(1)
    data.versions[0].aggregate.aggregated_readings!.replay_consistency.value = 0.2
    expect(() => parseEvaluations(data)).toThrow()
    const window = evaluationFixture()
    window.versions[0].aggregate.total_snapshot_count = 25
    window.versions[0].aggregate.distinct_run_count = 25
    window.versions[0].aggregate.valid_run_count = 25
    expect(() => parseEvaluations(window)).toThrow()
  })
  it('拒绝重复运行、跨版本绑定、错误顺序与重复决策所有权', () => {
    for (const mutate of [
      (data: ReturnType<typeof evaluationFixture>) => { data.versions[0].aggregate.sample_run_ids[1] = data.versions[0].aggregate.sample_run_ids[0] },
      (data: ReturnType<typeof evaluationFixture>) => { data.versions[0].sample_runs[0].skill_version = 'different' },
      (data: ReturnType<typeof evaluationFixture>) => { data.versions[0].sample_runs[1].completed_at = '2026-11-19T12:00:00Z' },
      (data: ReturnType<typeof evaluationFixture>) => { data.versions[0].sample_runs[1].decision_ids[0] = data.versions[0].sample_runs[0].decision_ids[0] },
    ]) { const data = evaluationFixture(); mutate(data); expect(() => parseEvaluations(data)).toThrow() }
  })
  it('拒绝不符合分子分母的读数与债务，并阻止不同口径比较', () => {
    const invalid = evaluationFixture()
    invalid.versions[0].sample_runs[0].readings.evidence_completeness.value = 1
    expect(() => parseEvaluations(invalid)).toThrow()
    const debt = evaluationFixture()
    debt.versions[0].aggregate.aggregated_readings!.uncertainty_debt.weighted_value = 4
    expect(() => parseEvaluations(debt)).toThrow()
    const { versions } = evaluationFixture()
    expect(compareVersions(versions[0], versions[1])).toBe('')
    expect(compareVersions(versions[0], versions[0])).toContain('不同')
    expect(compareVersions(versions[0], versions[2])).toContain('样本不足')
    versions[1].policy_version = 'other'
    expect(compareVersions(versions[0], versions[1])).toContain('口径')
    expect(formatDelta(versions[0].aggregate.aggregated_readings!, versions[1].aggregate.aggregated_readings!, 'evidence_completeness')).toBe('-80 个百分点')
  })
  it('切换数据源丢弃旧响应，错误不回退预览', async () => {
    let resolveRequest: (response: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { resolveRequest = resolve })))
    const scope = effectScope(), resource = scope.run(() => useEvaluations())!
    resource.mode.value = 'api'; await nextTick()
    expect(resource.loading.value).toBe(true)
    resource.mode.value = 'preview'; await nextTick()
    resolveRequest(new Response(JSON.stringify(evaluationFixture())))
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(resource.data.value?.source).toBe('frontend_preview')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 403 })))
    resource.mode.value = 'api'; await nextTick(); await new Promise(resolve => setTimeout(resolve, 0))
    expect(resource.data.value).toBe(null)
    expect(resource.error.value).toContain('无权')
    scope.stop()
  })
})
