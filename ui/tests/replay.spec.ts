import { describe, expect, it } from 'vitest'
import { previewDecisions, replayCoverage, type DecisionRecord } from '../src/components/workbench/audit'
import { parseReplay, replayBinding } from '../src/components/workbench/replay'
import { createPreview } from '../src/components/workbench/model'
const records = previewDecisions(createPreview('RUN-1', 'test', 'openJiuwen'))
function response() { return { source: 'ledger', run_id: 'RUN-1', replay_id: 'REPLAY-1', completed_at: '2026-11-19T12:00:00Z', tool_execution: false, items: replayBinding(records).map(item => ({ ...item, result: 'UNREPRODUCIBLE', reason: '缺少真实证据' })) } }
describe('规则回放响应', () => {
  it('前提检查与结果校验统一，规则缺快照不能报告一致，NONE不强制规则快照', () => {
    const complete: DecisionRecord = { ...records[1], gate: 'ALLOW', evidence_ref: 'EVIDENCE-1', rule_snapshot_ref: 'RULE-1', evidence: { evidence_id: 'EVIDENCE-1', input_digest: 'input', output_digest: 'output', model_id: 'fixture-model', model_params: '{}', verdict: 'ok', ts: '2026-11-19T12:00:00Z', content_hash: 'a'.repeat(64) } }
    function result(item: DecisionRecord) { return { ...response(), items: replayBinding([item]).map(binding => ({ ...binding, result: 'MATCH', reason: '合成一致结果' })) } }
    expect(replayCoverage([complete]).evaluable).toBe(1)
    expect(parseReplay(result(complete), 'RUN-1', [complete]).items[0].result).toBe('MATCH')
    for (const incomplete of [
      { ...complete, rule_snapshot_ref: null },
      { ...complete, evidence_ref: null },
      { ...complete, evidence: { ...complete.evidence!, content_hash: null } },
      { ...complete, evidence: { ...complete.evidence!, content_hash: 'invalid-hash' } },
    ]) {
      expect(replayCoverage([incomplete])).toMatchObject({ total: 1, evaluable: 0, missing: [incomplete.decision_id] })
      expect(() => parseReplay(result(incomplete), 'RUN-1', [incomplete])).toThrow()
    }
    const ungated = { ...complete, gate: 'NONE' as const, rule_snapshot_ref: null }
    expect(replayCoverage([ungated]).evaluable).toBe(1)
    expect(parseReplay(result(ungated), 'RUN-1', [ungated]).items[0].result).toBe('MATCH')
  })
  it('绑定原始运行与每项证据引用，不执行工具', () => { expect(parseReplay(response(), 'RUN-1', records).tool_execution).toBe(false) })
  it('拒绝伪造不一致缺证据、改动快照或漏项', () => {
    const mismatch = response(); mismatch.items[0].result = 'MISMATCH'
    expect(() => parseReplay(mismatch, 'RUN-1', records)).toThrow()
    const binding = response(); binding.items[0].evidence_ref = 'other'
    expect(() => parseReplay(binding, 'RUN-1', records)).toThrow()
    const partial = response(); partial.items.pop()
    expect(() => parseReplay(partial, 'RUN-1', records)).toThrow()
    expect(() => parseReplay(response(), 'OTHER', records)).toThrow()
  })
})
