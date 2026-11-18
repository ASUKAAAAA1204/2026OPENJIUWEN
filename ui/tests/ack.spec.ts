import { afterEach, describe, expect, it, vi } from 'vitest'
import { ACK_REASONS, ackReasonLabel, today } from '../src/utils/ack'

afterEach(() => {
  vi.useRealTimers()
})

describe('豁免原因码', () => {
  it('四个原因码且不重复', () => {
    expect(ACK_REASONS).toHaveLength(4)
    expect(new Set(ACK_REASONS.map((r) => r.code)).size).toBe(4)
  })

  it('已知原因码返回中文标签', () => {
    expect(ackReasonLabel('BUDGET_EXHAUSTED')).toBe('预算耗尽')
    expect(ackReasonLabel('LOW_SEVERITY')).toBe('低严重度')
  })

  it('未登记原因码不猜，返回兜底文案', () => {
    expect(ackReasonLabel('UNKNOWN')).toBe('未登记原因')
  })
})

describe('today', () => {
  it('输出 YYYY-MM-DD', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 11, 27))
    expect(today()).toBe('2026-12-27')
  })

  it('月份与日期补零', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 0, 5))
    expect(today()).toBe('2026-01-05')
  })
})
