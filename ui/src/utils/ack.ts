/** 豁免原因码：文案映射只服务 UI，不进入数据契约 */

export type AckReasonCode =
  | 'BUDGET_EXHAUSTED'
  | 'LOW_SEVERITY'
  | 'DEFERRED'
  | 'STRUCTURALLY_UNVERIFIABLE'

export interface AckRecord {
  code: AckReasonCode
  note: string
  date: string
}

export interface AckReason {
  code: AckReasonCode
  label: string
  hint: string
}

export const ACK_REASONS: AckReason[] = [
  { code: 'BUDGET_EXHAUSTED', label: '预算耗尽', hint: '修复预算已用尽，无法继续提升结论' },
  { code: 'LOW_SEVERITY', label: '低严重度', hint: '缺口不影响本次发布的关键结论' },
  { code: 'DEFERRED', label: '延后', hint: '计划在下一迭代补齐证据' },
  { code: 'STRUCTURALLY_UNVERIFIABLE', label: '结构性不可验', hint: '数据源本身无法提供可复算证据' },
]

export function ackReasonLabel(code: string): string {
  return ACK_REASONS.find((r) => r.code === code)?.label ?? '未登记原因'
}

/** 留痕日期 YYYY-MM-DD（仅展示，不参与任何判定） */
export function today(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
