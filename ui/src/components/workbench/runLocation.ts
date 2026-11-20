import type { Framework, RunPreview } from './model'
import { relatedRun } from './runs'
export const workspaceSections = ['工作台', '运行记录', '人工审批', '证据与债务', '版本评估', '设置'] as const
export type WorkspaceSection = typeof workspaceSections[number]
export function readWorkspaceSection(hash: string): WorkspaceSection | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const section = params.get('section')
  return workspaceSections.includes(section as WorkspaceSection) ? section as WorkspaceSection : null
}
export function writeWorkspaceLocation(section: WorkspaceSection) {
  return `#${new URLSearchParams({ section })}`
}
/** Only stable identifiers enter the URL; titles, parameters and credentials never do. */
export function readRunLocation(hash: string): RunPreview | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const id = params.get('run'), framework = params.get('framework')
  if (!id || !id.trim() || id !== id.trim() || id.length > 512 || !['openJiuwen', 'LangGraph', 'OpenAI Agents SDK'].includes(framework ?? '')) return null
  return relatedRun(id, framework as Framework)
}
export function writeRunLocation(run: RunPreview) {
  return `#${new URLSearchParams({ run: run.id, framework: run.framework })}`
}
export const decisionViews = ['判定与参数', '事件时间线', '证据与账本', '回放'] as const
export function readDecisionLocation(hash: string, runId: string) {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  if (params.get('run') !== runId) return { decision: '', view: decisionViews[0] as string }
  const raw = params.get('decision') ?? '', view = params.get('view') ?? ''
  return { decision: raw.trim() === raw && raw.length <= 512 ? raw : '', view: decisionViews.includes(view as typeof decisionViews[number]) ? view : decisionViews[0] }
}
export function writeDecisionLocation(run: RunPreview, decision: string, view: string) {
  const params = new URLSearchParams(writeRunLocation(run).slice(1))
  if (decision && decision.trim() === decision && decision.length <= 512) params.set('decision', decision)
  if (decisionViews.includes(view as typeof decisionViews[number])) params.set('view', view)
  return `#${params}`
}
