import { hasReplayPrerequisites, type DecisionRecord } from './audit'
export interface ReplayItem { decision_id: string; result: 'MATCH' | 'MISMATCH' | 'UNREPRODUCIBLE'; reason: string; evidence_ref: string | null; content_hash: string | null; rule_snapshot_ref: string | null }
export interface ReplayReport { source: 'ledger'; run_id: string; replay_id: string; completed_at: string; tool_execution: false; items: ReplayItem[] }
export function replayBinding(records: DecisionRecord[]) {
  return records.map(item => ({ decision_id: item.decision_id, evidence_ref: item.evidence_ref, content_hash: item.evidence?.content_hash ?? null, rule_snapshot_ref: item.rule_snapshot_ref }))
}
export function parseReplay(value: unknown, runId: string, records: DecisionRecord[]): ReplayReport {
  const fail = () => { throw new Error('回放结果与原运行或证据快照不一致。') }
  if (typeof value !== 'object' || value === null) return fail()
  const report = value as Record<string, unknown>
  if (report.source !== 'ledger' || report.run_id !== runId || typeof report.replay_id !== 'string' || !report.replay_id.trim() || typeof report.completed_at !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(report.completed_at) || !Number.isFinite(Date.parse(report.completed_at)) || report.tool_execution !== false || !Array.isArray(report.items) || report.items.length !== records.length) return fail()
  const binding = replayBinding(records)
  for (let i = 0; i < binding.length; i++) {
    const item = report.items[i] as Record<string, unknown> | null, expected = binding[i]
    if (!item || typeof item !== 'object' || !['MATCH', 'MISMATCH', 'UNREPRODUCIBLE'].includes(String(item.result)) || typeof item.reason !== 'string' || !item.reason.trim() || (Object.keys(expected) as (keyof typeof expected)[]).some(key => item[key] !== expected[key])) return fail()
    if (!hasReplayPrerequisites(records[i]) && item.result !== 'UNREPRODUCIBLE') return fail()
  }
  return report as unknown as ReplayReport
}
