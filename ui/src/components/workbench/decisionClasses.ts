import type { DecisionRecord } from './audit'
/** Frozen names from 关键决策白名单 v1.0; a new name requires a version change. */
export const decisionClasses = [
  ['file_write', '文件写入'], ['file_delete', '删除与移动'], ['exec', '命令执行'],
  ['network_write', '对外写入'], ['vcs_publish', '代码发布'], ['policy_decision', '策略干预'],
  ['human_ack', '人工答复'], ['delegate', '子任务委派'], ['abort', '显式中止'],
  ['run_close', '运行关闭'], ['final_claim', '终稿断言'],
] as const
export function decisionClassKey(raw: string) {
  const canonical = raw.toLowerCase()
  return decisionClasses.some(([key]) => key === canonical) ? canonical : `other:${raw}`
}
export function decisionClassCounts(records: DecisionRecord[]) {
  const counts = new Map<string, number>()
  for (const record of records) {
    const key = decisionClassKey(record.class)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const known = decisionClasses.map(([key, label]) => ({ key, label, count: counts.get(key) ?? 0 }))
  const other = [...counts].filter(([key]) => key.startsWith('other:')).map(([key, count]) => ({ key, label: key.slice(6), count }))
  return { known, other, observed: known.filter(item => item.count > 0).length }
}
