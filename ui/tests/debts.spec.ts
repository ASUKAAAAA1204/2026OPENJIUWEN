import { effectScope } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { createDebtDemo, debtSummary, normalizeDebtItems, parseDebtRuns, runDebtSummary, useDebts, validateResolution } from '../src/components/workbench/debts'
import DebtWorkspace from '../src/components/workbench/DebtWorkspace.vue'

describe('不确定债务', () => {
  it('父级保留自身债务并汇总后代，重复引用不双计，零债务子运行仍可显示', () => {
    const { items, runs } = createDebtDemo()
    const extended = [...runs!, { ...runs![1], run_id: 'GRANDCHILD', parent_run_id: runs![1].run_id }]
    const parsed = parseDebtRuns(extended, items)!
    expect(runDebtSummary([...items, items[2]], parsed, 'RUN-DEBT-DEMO')).toEqual({ own: 5, children: 1, total: 6, count: 3 })
    expect(runDebtSummary(items, parsed, 'RUN-DEBT-CHILD')).toEqual({ own: 1, children: 0, total: 1, count: 1 })
    expect(runDebtSummary(items, parsed, 'GRANDCHILD')).toEqual({ own: 0, children: 0, total: 0, count: 0 })
  })
  it('缺少元数据不能推断关系，部分关系、循环及超过三层委派被拒绝', () => {
    const { items, runs } = createDebtDemo()
    expect(parseDebtRuns(undefined, items)).toBeNull()
    expect(() => parseDebtRuns([runs![0]], items)).toThrow('所有权')
    expect(() => parseDebtRuns([runs![1]], [])).toThrow('缺失')
    expect(() => parseDebtRuns([{ ...runs![0], parent_run_id: runs![1].run_id }, runs![1]], items)).toThrow('循环')
    const chain = Array.from({ length: 5 }, (_, index) => ({ ...runs![0], run_id: `R${index}`, parent_run_id: index ? `R${index - 1}` : null }))
    expect(parseDebtRuns(chain.slice(0, 4), [])).toHaveLength(4)
    expect(() => parseDebtRuns(chain, [])).toThrow('深度')
  })
  it('无效阈值、重复运行和不可能的结束读数拒绝展示', () => {
    const run = createDebtDemo().runs![0]
    for (const review_threshold of [-1, NaN, Infinity, '5', true]) {
      expect(() => parseDebtRuns([{ ...run, review_threshold }], [])).toThrow()
    }
    expect(parseDebtRuns([{ ...run, review_threshold: 0 }], [])![0].review_threshold).toBe(0)
    expect(parseDebtRuns([{ ...run, review_threshold: 0.5 }], [])![0].review_threshold).toBe(0.5)
    expect(() => parseDebtRuns([run, run], [])).toThrow()
    expect(() => parseDebtRuns([{ ...run, final_reading: { total: 4, count: 1 } }], [])).toThrow()
    expect(() => parseDebtRuns([{ ...run, final_reading: { total: 0, count: 1 } }], [])).toThrow()
  })
  it('消解更新当前汇总，结束时读数与待复核事实保留', async () => {
    const scope = effectScope(), debt = scope.run(useDebts)!
    await debt.resolve(debt.data.value!.items[0], { evidence_id: 'evidence:verified', content_hash: 'a'.repeat(64), regate_decision_id: '', confirmed: true })
    const snapshot = debt.data.value!
    expect(runDebtSummary(snapshot.items, snapshot.runs!, 'RUN-DEBT-DEMO')).toEqual({ own: 2, children: 1, total: 3, count: 2 })
    expect(snapshot.runs![0].review_status).toBe('queued')
    expect(snapshot.runs![0].final_reading).toEqual({ total: 5, count: 2 })
    scope.stop()
  })
  it('影响衰减不减少权重，重复子运行引用不双计，同决策不同类型保留', () => {
    const data = createDebtDemo()
    expect(data.items[0].display_severity).toBe('mid')
    expect(debtSummary([...data.items, data.items[2]])).toEqual({ total: 6, open: 3, retained: 3 })
    const reordered = Object.fromEntries(Object.entries(data.items[2]).reverse())
    expect(debtSummary(normalizeDebtItems([...data.items, reordered])).total).toBe(6)
    const secondType = { ...data.items[1], decision_id: data.items[0].decision_id }
    expect(debtSummary([data.items[0], secondType]).total).toBe(5)
  })
  it('冲突重复项、原始权重篡改和错误消解方式被拒绝', () => {
    const item = createDebtDemo().items[0]
    expect(() => normalizeDebtItems([item, { ...item, weight: 1 }])).toThrow()
    expect(() => normalizeDebtItems([item, { ...item, cause: '冲突' }])).toThrow()
    expect(() => normalizeDebtItems([{ ...item, status: 'resolved', resolved: { seq: 9, method: 'unify_sources', evidence_ref: { evidence_id: 'x', content_hash: 'a'.repeat(64) }, regate_success: null } }])).toThrow()
  })
  it('修正参数需提供重新过闸记录，不接受任意引用或缺核对', () => {
    const item = createDebtDemo().items[2]
    const input = { evidence_id: 'evidence:test', content_hash: 'a'.repeat(64), regate_decision_id: '', confirmed: true }
    expect(validateResolution(item, input)).toContain('重新过闸')
    expect(validateResolution(item, { ...input, regate_decision_id: 'decision:regate', content_hash: '任意引用' })).toContain('64位')
    expect(validateResolution(item, { ...input, regate_decision_id: 'decision:regate', confirmed: false })).toContain('确认')
  })
  it('演示消解只追加投影信息，原条目不变，旧引用不能重复处理', async () => {
    const scope = effectScope(), debt = scope.run(useDebts)!
    const original = debt.data.value!.items[0]
    const input = { evidence_id: 'evidence:test', content_hash: 'a'.repeat(64), regate_decision_id: '', confirmed: true }
    expect(await debt.resolve(original, input)).toBe(true)
    expect(original.status).toBe('open')
    expect(debtSummary(debt.data.value!.items)).toEqual({ total: 3, open: 2, retained: 3 })
    const updated = debt.data.value!.items[0]
    expect(updated.resolved?.seq).toBeGreaterThan(original.opened_seq)
    expect(updated.weight).toBe(original.weight)
    expect(await debt.resolve(original, input)).toBe(false)
    scope.stop()
  })
  it('页面显示验证失败，核对完整依据后保留追加历史', async () => {
    const page = mount(DebtWorkspace)
    await page.get('form').trigger('submit')
    expect(page.get('[role=alert]').text()).toContain('证据标识')
    await page.get('#resolve-ref').setValue('demo:evidence')
    await page.get('#resolve-hash').setValue('a'.repeat(64))
    await page.get('input[type=checkbox]').setValue(true)
    await page.get('#resolve-ref').setValue('demo:evidence-updated')
    expect((page.get('input[type=checkbox]').element as HTMLInputElement).checked).toBe(false)
    await page.get('input[type=checkbox]').setValue(true)
    await page.get('form').trigger('submit')
    expect(page.get('.summary-grid').text()).toContain('未消解加权债务3')
    expect(page.get('.debt-history').text()).toContain('原始登记')
    expect(page.get('.debt-history').text()).toContain('demo:evidence-updated')
    expect(page.find('form').exists()).toBe(false)
    page.unmount()
  })
})

vi.mock('../src/components/workbench/debts', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/components/workbench/debts')>()
  return { ...actual, useDebts: () => actual.useDebts('preview') }
})
