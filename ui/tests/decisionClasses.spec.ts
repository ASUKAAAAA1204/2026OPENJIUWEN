import { describe, expect, it } from 'vitest'
import { decisionClasses, decisionClassCounts, decisionClassKey } from '../src/components/workbench/decisionClasses'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'
describe('白名单类别观察计数', () => {
  const base = previewDecisions(createPreview('RUN-1', 'test', 'openJiuwen'))[0]
  it('冻结11类，大小写已知别名映射但未知值保留', () => {
    expect(decisionClasses).toHaveLength(11)
    expect(decisionClassKey('FILE_WRITE')).toBe('file_write')
    expect(decisionClassKey('exec')).toBe('exec')
    expect(decisionClassKey('UNCLASSIFIED')).toBe('other:UNCLASSIFIED')
    expect(decisionClassKey('exec ')).toBe('other:exec ')
  })
  it('观察计数保留所有记录，不把未知或预览计入11类，也不修改原分类', () => {
    const records = ['file_write', 'FILE_WRITE', 'delegate', 'UNCLASSIFIED', 'READ_ONLY_PREVIEW'].map((value, i) => ({ ...base, decision_id: String(i), class: value }))
    const original = JSON.stringify(records), result = decisionClassCounts(records)
    expect(result.observed).toBe(2)
    expect(result.known[0].count).toBe(2)
    expect(result.other).toHaveLength(2)
    expect([...result.known, ...result.other].reduce((sum, item) => sum + item.count, 0)).toBe(5)
    expect(JSON.stringify(records)).toBe(original)
  })
  it('空运行展示11类零观察，全类样本均计入', () => {
    expect(decisionClassCounts([]).observed).toBe(0)
    expect(decisionClassCounts([]).known.every(item => item.count === 0)).toBe(true)
    expect(decisionClassCounts(decisionClasses.map(([value], i) => ({ ...base, decision_id: String(i), class: value }))).observed).toBe(11)
  })
})
