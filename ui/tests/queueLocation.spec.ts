import { expect, it } from 'vitest'
import { readQueueLocation } from '../src/components/workbench/queueLocation'
it('业务位置仅接受当前页面来源、筛选与有界标识', () => {
  expect(readQueueLocation('#section=人工审批&queue_source=api&queue_filter=全部&queue_item=AP-2&note=secret&confirm=true', '人工审批')).toEqual({ source: 'api', filter: '全部', selected: 'AP-2' })
  expect(readQueueLocation('#section=证据与债务&queue_source=evil&queue_filter=evil&queue_item=%20bad', '证据与债务')).toEqual({ source: null, filter: '未消解', selected: '' })
  expect(readQueueLocation('#section=人工审批', '证据与债务')).toBeNull()
  expect(readQueueLocation(`#section=人工审批&queue_item=${'x'.repeat(513)}`, '人工审批')?.selected).toBe('')
})
