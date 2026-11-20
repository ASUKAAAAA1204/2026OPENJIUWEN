import { effectScope } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApprovalDemo, parseApprovals, useApprovals } from '../src/components/workbench/approvals'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
describe('审批约束', () => {
  it('拒绝高危超时放行及重复审批编号', () => {
    const items = createApprovalDemo()
    expect(() => parseApprovals([{ ...items[0], timeout_policy: 'ALLOW' }])).toThrow()
    expect(() => parseApprovals([items[0], items[0]])).toThrow()
  })
  it('到期不生成批准结果，刷新不延长截止时间', async () => {
    vi.useFakeTimers()
    const scope = effectScope()
    const queue = scope.run(useApprovals)!
    const item = queue.items.value[0]
    await vi.advanceTimersByTimeAsync(31000)
    expect(await queue.answer(item, 'approve', '', true)).toBe(false)
    expect(queue.pendingCount.value).toBe(0)
    expect(item.response).toBeNull()
    await queue.load()
    expect(queue.items.value[0].expires_at).toBe(item.expires_at)
    scope.stop()
  })
  it('同一旧对象不能重复批准', async () => {
    const scope = effectScope(), queue = scope.run(useApprovals)!
    const item = queue.items.value[0]
    expect(await queue.answer(item, 'approve', '已核查', true)).toBe(true)
    expect(await queue.answer(item, 'approve', '再次', true)).toBe(false)
    expect(queue.items.value[0].response?.approved_args_digest).toBe(item.args_digest)
    scope.stop()
  })
})
