import { describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { parseRunPage, useRuns } from '../src/components/workbench/runs'
export function runFixture() { return { source: 'ledger', items: [{ id: 'RUN-1', title: '检查文档', source: 'ledger', framework: 'LangGraph', tools: [], state: 'degraded', segment: 2, started_at: '2026-11-19T12:00:00Z', completed_at: null, parent_run_id: 'PARENT-1', child_run_ids: ['CHILD-1'], decision_count: 3 }], next_cursor: 'page2' } }
describe('服务运行列表', () => {
  it('已取消的旧分页403不能清除新查询结果', async () => {
    const scope = effectScope()
    const runs = scope.run(useRuns)!
    let rejectOld!: (value: Response) => void
    const fixture = runFixture()
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(fixture)))
      .mockImplementationOnce(() => new Promise<Response>(resolve => { rejectOld = resolve }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...fixture, items: [{ ...fixture.items[0], id: 'NEW-RUN' }], next_cursor: null })))
    vi.stubGlobal('fetch', fetchMock)
    try {
      await runs.load()
      const old = runs.load(true)
      runs.query.value = 'new'
      await runs.load()
      rejectOld(new Response('{}', { status: 403 }))
      await old
      expect(runs.items.value.map(item => item.id)).toEqual(['NEW-RUN'])
      expect(runs.error.value).toBe('')
      expect(fetchMock.mock.calls.every(call => call[1].cache === 'no-store')).toBe(true)
    } finally { scope.stop(); vi.unstubAllGlobals() }
  })
  it('保留降级运行及续跑段号，不伪造工具', () => {
    expect(parseRunPage(runFixture()).items[0].segment).toBe(2)
  })
  it('拒绝重复运行、闭合缺时间及循环关系', () => {
    const duplicate = runFixture(); duplicate.items.push(duplicate.items[0])
    expect(() => parseRunPage(duplicate)).toThrow()
    const closed = runFixture(); closed.items[0].state = 'closed'
    expect(() => parseRunPage(closed)).toThrow()
    const cycle = runFixture(); cycle.items[0].child_run_ids.push('PARENT-1')
    expect(() => parseRunPage(cycle)).toThrow()
  })
})
