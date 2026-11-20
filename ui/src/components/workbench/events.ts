export const eventLabels = { GATE_DECIDED: '策略判定已记录', ASK_REQUESTED: '已请求人工审批', ASK_ANSWERED: '人工应答已记录', REWRITE_VALIDATED: '改写重判已通过', EXEC_STARTED: '工具执行已开始', EXEC_FINISHED: '工具执行已完成', EXEC_FAILED: '工具执行失败', UNKNOWN_OUTCOME: '执行结果未知' } as const
export interface DecisionEvent { event_id: string; seq: number; type: keyof typeof eventLabels; recorded_at: string | null; summary: string }
export function parseEvents(value: unknown, runId: string, decisionId: string): DecisionEvent[] {
  const data = value as Record<string, unknown> | null
  if (!data || data.source !== 'ledger' || data.run_id !== runId || data.decision_id !== decisionId || !Array.isArray(data.events)) throw new Error('事件响应与当前决策不匹配。')
  const ids = new Set<string>(); let previous = 0
  return data.events.map((raw: unknown) => {
    const item = raw as DecisionEvent | null
    if (!item || typeof item.event_id !== 'string' || !item.event_id.trim() || ids.has(item.event_id) || !Number.isSafeInteger(item.seq) || item.seq <= previous || !Object.prototype.hasOwnProperty.call(eventLabels, item.type) || typeof item.summary !== 'string' || !item.summary.trim() || !(item.recorded_at === null || (typeof item.recorded_at === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(item.recorded_at) && Number.isFinite(Date.parse(item.recorded_at))))) throw new Error('事件格式或追加顺序无效，无法展示可信时间线。')
    ids.add(item.event_id); previous = item.seq
    return item
  })
}
