import type { CreateRunInput, LifecycleSnapshot, RunContext } from '../../src/components/workbench/lifecycle'
export function runContextFixture(): RunContext {
  return { source: 'ledger', revision: 'context-1', can_create: true, workspaces: [{ id: 'workspace-1', path: 'D:/controlled/workspace' }], frameworks: [{ name: 'openJiuwen', version: '0.1.20.post1', available: true, reason: '合成接入验证，不代表实际框架已联调' }, { name: 'LangGraph', version: 'fixture-version', available: true, reason: '合成接入状态' }, { name: 'OpenAI Agents SDK', version: 'fixture-version', available: false, reason: '此样例未开放接入' }], skills: [{ id: 'code-assistant', version: '1.0' }] }
}
export const createRunFixture: CreateRunInput = { task_id: 'BUSINESS-42', title: '核查项目文件并生成修改建议', framework: 'openJiuwen', workspace_id: 'workspace-1', skill_id: 'code-assistant', skill_version: '1.0' }
export function lifecycleFixture(): LifecycleSnapshot {
  return { source: 'ledger', revision: 'lifecycle-1', task_id: createRunFixture.task_id, workspace_id: createRunFixture.workspace_id, skill_id: createRunFixture.skill_id, skill_version: createRunFixture.skill_version, can_close: true, can_resume: true, reason: '合成中断状态：无在途工具，允许登记恢复段。', run: { source: 'ledger', id: 'RUN-LIFECYCLE-42', title: createRunFixture.title, framework: 'openJiuwen', tools: [], state: 'open', segment: 1, started_at: '2026-11-19T12:00:00Z', completed_at: null, parent_run_id: null, child_run_ids: [], decision_count: 0 } }
}
