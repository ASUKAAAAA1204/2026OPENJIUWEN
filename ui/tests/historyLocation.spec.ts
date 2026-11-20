import { expect, it } from 'vitest'
import { readHistoryLocation, writeHistoryLocation } from '../src/components/workbench/historyLocation'
it('仅恢复运行记录白名单筛选，不序列化搜索与游标', () => {
  const value = { source: 'api' as const, framework: 'LangGraph', state: 'closed' }
  expect(readHistoryLocation(writeHistoryLocation(value))).toEqual(value)
  expect(readHistoryLocation('#section=设置&run_source=api')).toBeNull()
  expect(readHistoryLocation('#section=运行记录&run_source=unknown')).toBeNull()
  expect(readHistoryLocation('#section=运行记录&run_source=api&run_framework=evil&run_state=bad')).toEqual({ source: 'api', framework: '全部框架', state: '全部状态' })
  expect(writeHistoryLocation(value)).not.toMatch(/cursor|query|q=/)
})
