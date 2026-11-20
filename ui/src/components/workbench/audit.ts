import type { RunPreview } from './model'

export type Gate = 'ALLOW' | 'ASK' | 'DENY' | 'REWRITE' | 'NONE'
export interface DecisionRecord {
  decision_id: string
  run_id: string
  segment: number
  class: string
  tool_name: string
  args_digest: string
  args_after: string | null
  whitelist_version: string
  policy_version: string
  rule_snapshot_ref: string | null
  gate: Gate
  gate_reason: string
  gate_latency_ms: number | null
  exec_policy: 'normal' | 'degrade'
  status: string
  evidence_ref: string | null
  evidence: {
    evidence_id: string
    input_digest: string
    output_digest: string
    model_id: string
    model_params: string
    verdict: string
    ts: string | null
    content_hash: string | null
  } | null
}

/** 界面预览映射，绝不能作为账本事件、规则快照或真实执行结果。 */
export function previewDecisions(run: RunPreview): DecisionRecord[] {
  return run.tools.map(tool => ({
    decision_id: `${run.id}:${tool.id}`,
    run_id: run.id,
    segment: 1,
    class: tool.gate === 'NONE' ? 'READ_ONLY_PREVIEW' : 'FILE_WRITE',
    tool_name: tool.name,
    args_digest: JSON.stringify(tool.args, null, 2),
    args_after: null,
    whitelist_version: '未加载',
    policy_version: '未加载',
    rule_snapshot_ref: null,
    gate: tool.gate,
    gate_reason: tool.gate === 'NONE' ? '只读界面预览，未运行规则判定。' : '预览放行标签，未运行规则判定，不能据此执行。',
    gate_latency_ms: null,
    exec_policy: 'normal',
    status: 'PREVIEW_ONLY',
    evidence_ref: null,
    evidence: null,
  }))
}

export interface ReplaySummary {
  total: number
  evaluable: number
  matched: number
  missing: string[]
}
/** 仅检查回放前提，不在浏览器伪造规则重判。缺证据仍保留在分母中。 */
export function hasReplayPrerequisites(item: DecisionRecord): boolean {
  return !!item.evidence && !!item.evidence_ref?.trim() && /^[a-f0-9]{64}$/.test(item.evidence.content_hash ?? '') &&
    (item.gate === 'NONE' || !!item.rule_snapshot_ref)
}
export function replayCoverage(records: DecisionRecord[]): ReplaySummary {
  const missing = records.filter(item => !hasReplayPrerequisites(item)).map(item => item.decision_id)
  return { total: records.length, evaluable: records.length - missing.length, matched: 0, missing }
}

export function buildPreviewExport(run: RunPreview) {
  return {
    schema: 'temisguard.frontend-preview.v1',
    source: 'frontend_preview',
    warning: '未执行工具；不是账本导出，不含真实证据与哈希链。',
    run: { id: run.id, title: run.title, framework: run.framework },
    decisions: previewDecisions(run),
  }
}
