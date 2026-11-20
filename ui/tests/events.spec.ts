import { describe, expect, it } from 'vitest'
import { parseEvents } from '../src/components/workbench/events'
const event = { event_id: 'E1', seq: 1, type: 'GATE_DECIDED', recorded_at: null, summary: '允许不代表完成' }
const snapshot = { source: 'ledger', run_id: 'R', decision_id: 'D', events: [event] }
describe('账本事件边界', () => {
  it('允许缺时间但保留追加序号', () => { expect(parseEvents(snapshot, 'R', 'D')[0].recorded_at).toBeNull() })
  it('拒绝跨决策、重复事件、倒序和未知事件类型', () => {
    expect(() => parseEvents(snapshot, 'R', 'OTHER')).toThrow()
    for (const next of [event, { ...event, event_id: 'E2', seq: 0 }, { ...event, type: 'FAKE_COMPLETED' }, { ...event, recorded_at: '2026-11-19' }]) expect(() => parseEvents({ ...snapshot, events: [event, next] }, 'R', 'D')).toThrow()
  })
})
