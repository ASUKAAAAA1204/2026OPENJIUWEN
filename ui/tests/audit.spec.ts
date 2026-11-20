import { describe, expect, it } from 'vitest'
import { buildPreviewExport, previewDecisions, replayCoverage } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'

describe('运行审计预览边界', () => {
  const run = createPreview('RUN-TEST', '核查', 'LangGraph')
  it('导出标明来源且不生成执行结果或哈希', () => {
    const output = buildPreviewExport(run)
    expect(output.source).toBe('frontend_preview')
    expect(output.decisions.every(record => record.status === 'PREVIEW_ONLY' && record.evidence === null && record.rule_snapshot_ref === null)).toBe(true)
    expect(output.decisions[1].args_digest).toContain('proposal.md')
  })
  it('缺证据项保留在回放分母，不报告虚假的一致率', () => {
    const result = replayCoverage(previewDecisions(run))
    expect(result).toEqual({ total: 2, evaluable: 0, matched: 0, missing: ['RUN-TEST:read', 'RUN-TEST:write'] })
  })
})
