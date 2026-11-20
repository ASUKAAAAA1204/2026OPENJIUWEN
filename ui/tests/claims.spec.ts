import { describe, expect, it } from 'vitest'
import { parseClaims } from '../src/components/workbench/claims'
export const claimFixture = () => ({ claim_id: 'CLAIM-1', text: '项目共有3个模块', locator: 'report.md:12', source_ref: 'artifact:report-v1', decision_id: 'DECISION-1', evidence_ref: 'EVIDENCE-1', location_status: 'LOCATED', verification: 'MATCH', missing_decision_mark: null })
export const missingClaimFixture = () => ({ ...claimFixture(), claim_id: 'CLAIM-2', text: '项目已经发布', locator: null, decision_id: null, evidence_ref: null, location_status: 'UNLOCATED', verification: 'UNKNOWN', missing_decision_mark: { mark_type: 'NO_EVIDENCE', level: 'high', review_ref: 'REVIEW-1' } })
describe('断言独立记录与缺决策风险', () => {
  const snapshot = (claims: unknown[]) => ({ source: 'ledger', run_id: 'RUN-1', claims })
  it('同一决策可关联多断言，空集合保留为空', () => {
    expect(parseClaims(snapshot([claimFixture(), { ...claimFixture(), claim_id: 'CLAIM-3' }]), 'RUN-1')).toHaveLength(2)
    expect(parseClaims(snapshot([]), 'RUN-1')).toEqual([])
  })
  it('缺决策必须是未知风险，要求high标记和复核引用', () => {
    expect(parseClaims(snapshot([missingClaimFixture()]), 'RUN-1')).toHaveLength(1)
    for (const patch of [{ verification: 'MATCH' }, { missing_decision_mark: null }, { missing_decision_mark: { mark_type: 'NO_EVIDENCE', level: 'low', review_ref: 'R' } }]) expect(() => parseClaims(snapshot([{ ...missingClaimFixture(), ...patch }]), 'RUN-1')).toThrow()
  })
  it('拒绝错运行、重复标识、定位矛盾和无证据的一致结论', () => {
    expect(() => parseClaims(snapshot([claimFixture()]), 'WRONG')).toThrow()
    expect(() => parseClaims(snapshot([claimFixture(), claimFixture()]), 'RUN-1')).toThrow()
    for (const patch of [{ locator: null }, { location_status: 'UNLOCATED' }, { evidence_ref: null }, { source_ref: '' }]) expect(() => parseClaims(snapshot([{ ...claimFixture(), ...patch }]), 'RUN-1')).toThrow()
  })
})
