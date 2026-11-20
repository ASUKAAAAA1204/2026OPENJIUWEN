export interface ClaimRecord {
  claim_id: string; text: string; locator: string | null; source_ref: string
  decision_id: string | null; evidence_ref: string | null
  location_status: 'LOCATED' | 'UNLOCATED' | 'UNVERIFIABLE'
  verification: 'MATCH' | 'MISMATCH' | 'UNKNOWN'
  missing_decision_mark: { mark_type: 'NO_EVIDENCE'; level: 'high'; review_ref: string } | null
}
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
export function parseClaims(value: unknown, runId: string): ClaimRecord[] {
  const fail = () => { throw new Error('断言响应、定位或缺决策风险记录不符合契约，已停止展示。') }
  if (!object(value) || value.source !== 'ledger' || value.run_id !== runId || !Array.isArray(value.claims)) return fail()
  const ids = new Set<string>()
  return value.claims.map(raw => {
    if (!object(raw) || !text(raw.claim_id) || ids.has(raw.claim_id) || !text(raw.text) || !text(raw.source_ref) ||
      !(raw.locator === null || text(raw.locator)) || !(raw.decision_id === null || text(raw.decision_id)) || !(raw.evidence_ref === null || text(raw.evidence_ref)) ||
      !['LOCATED', 'UNLOCATED', 'UNVERIFIABLE'].includes(String(raw.location_status)) || !['MATCH', 'MISMATCH', 'UNKNOWN'].includes(String(raw.verification))) return fail()
    if ((raw.location_status === 'LOCATED') !== (raw.locator !== null)) return fail()
    if (raw.decision_id === null) {
      const mark = raw.missing_decision_mark
      if (raw.verification !== 'UNKNOWN' || raw.evidence_ref !== null || !object(mark) || mark.mark_type !== 'NO_EVIDENCE' || mark.level !== 'high' || !text(mark.review_ref)) return fail()
    } else if (raw.missing_decision_mark !== null || (raw.verification === 'MATCH' && (raw.evidence_ref === null || raw.location_status !== 'LOCATED'))) return fail()
    ids.add(raw.claim_id)
    return raw as unknown as ClaimRecord
  })
}
