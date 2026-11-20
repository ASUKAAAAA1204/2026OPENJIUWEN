/** Synthetic HTTP fixtures. These are not real ledger or model-integration evidence. */
import { rateKeys, type EvaluationSnapshot, type Rate, type Readings, type SampleRun, type VersionEvaluation, type AggregateReadings } from '../../src/components/workbench/evaluations'
export function evaluationFixture(): EvaluationSnapshot {
  function version(name: string, sampleCount: number, zero = false): VersionEvaluation {
    const runs: SampleRun[] = Array.from({ length: sampleCount }, (_, index) => {
      const id = `${name}-RUN-${index}`
      const denominator = index === 0 ? 2 : 10
      const rate: Rate = { numerator: zero ? 0 : index === 0 ? 0 : denominator, denominator, value: zero ? 0 : index === 0 ? 0 : 1, status: 'VALUE' }
      const readings = Object.fromEntries(rateKeys.map(key => [key, { ...rate }])) as unknown as Readings
      readings.replay_consistency = { numerator: 0, denominator: 0, value: null, status: 'NOT_APPLICABLE' }
      readings.reproducibility.evaluable_coverage = { ...rate }
      readings.uncertainty_debt = { weighted_value: zero ? 0 : 2, unresolved_count: zero ? 0 : 1 }
      return { run_id: id, skill_id: 'code-assistant', skill_version: name, status: 'closed', completed_at: new Date(Date.UTC(2026, 10, 18, 12, 0, -index)).toISOString(), valid_for_aggregation: true, whitelist_decision_count: denominator, decision_ids: Array.from({ length: denominator }, (_, i) => `${id}-DEC-${i}`), decision_classes: [['FILE_READ', 'FILE_WRITE', 'COMMAND_EXEC'][index % 3]], readings }
    })
    const aggregated = Object.fromEntries(rateKeys.map(key => [key, key === 'replay_consistency' ? { value: null, status: 'NOT_APPLICABLE', runs_considered: 0 } : { value: zero ? 0 : (sampleCount - 1) / sampleCount, status: 'VALUE', runs_considered: sampleCount }])) as unknown as AggregateReadings
    aggregated.reproducibility.evaluable_coverage = { ...aggregated.reproducibility }
    aggregated.uncertainty_debt = { weighted_value: zero ? 0 : 2, unresolved_count: zero ? 0 : 1 }
    return { policy_version: 'policy-2.7', whitelist_version: 'whitelist-2.7', whitelist_classes: ['FILE_READ', 'FILE_WRITE', 'COMMAND_EXEC'], aggregate: { schema_version: 'temisguard-b-v2.7', skill_id: 'code-assistant', skill_version: name, total_snapshot_count: sampleCount, distinct_run_count: sampleCount, valid_run_count: sampleCount, sample_run_count: sampleCount, class_count: Math.min(3, sampleCount), sample_run_ids: runs.map(run => run.run_id), status: sampleCount >= 5 ? 'ok' : '样本不足', aggregated_readings: sampleCount >= 5 ? aggregated : null }, sample_runs: runs }
  }
  return { source: 'ledger', revision: 'fixture-1', generated_at: '2026-11-18T12:01:00Z', comparison_enabled: true, versions: [version('1.0', 5), version('1.1', 5, true), version('1.2', 2)] }
}
