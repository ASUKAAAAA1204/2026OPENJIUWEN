import { describe, expect, it } from 'vitest'
import { parseLifecycle, parseLifecycleReceipt, parseRunContext, validateCreate } from '../src/components/workbench/lifecycle'
import { createRunFixture, lifecycleFixture, runContextFixture } from './fixtures/lifecycle'
describe('运行生命周期绑定', () => {
  it('创建只接受授权目录、可用框架及服务端提供的 Skill 版本', () => {
    const context = parseRunContext(runContextFixture())
    expect(validateCreate(createRunFixture, context)).toBe('')
    expect(validateCreate({ ...createRunFixture, workspace_id: '../outside' }, context)).toContain('授权')
    expect(validateCreate({ ...createRunFixture, framework: 'OpenAI Agents SDK' }, context)).toContain('不可用')
    expect(validateCreate({ ...createRunFixture, skill_version: 'fake' }, context)).toContain('Skill')
    expect(validateCreate({ ...createRunFixture, task_id: ' ' }, context)).toContain('业务任务')
  })
  it('拒绝宣称错误 openJiuwen 锁定版本可用及重复目录', () => {
    const context = runContextFixture()
    context.frameworks[0].version = 'unexpected'
    expect(() => parseRunContext(context)).toThrow()
    context.frameworks[0].version = '0.1.20.post1'; context.workspaces.push(context.workspaces[0])
    expect(() => parseRunContext(context)).toThrow()
  })
  it('关闭运行必须只读，不允许前端启用恢复', () => {
    const snapshot = lifecycleFixture()
    snapshot.run.state = 'closed'; snapshot.run.completed_at = '2026-11-19T13:00:00Z'
    expect(() => parseLifecycle(snapshot)).toThrow()
    snapshot.can_close = false; snapshot.can_resume = false
    expect(parseLifecycle(snapshot).run.state).toBe('closed')
  })
  it('恢复必须保持运行标识，精确递增一段并更新修订', () => {
    const before = lifecycleFixture(), after = lifecycleFixture()
    after.revision = 'lifecycle-2'; after.run.segment = 2; after.can_resume = false
    const receipt = { source: 'ledger', action: 'resume', request_id: 'request', audit_id: 'audit', created: false, snapshot: after }
    expect(parseLifecycleReceipt(receipt, 'resume', 'request', before).snapshot.run.segment).toBe(2)
    after.run.segment = 3
    expect(() => parseLifecycleReceipt(receipt, 'resume', 'request', before)).toThrow()
    after.run.segment = 2; after.run.id = 'NEW-RUN'
    expect(() => parseLifecycleReceipt(receipt, 'resume', 'request', before)).toThrow()
  })
  it('任务去重可返回原运行，但不能伪称创建了已有决策的运行', () => {
    const snapshot = lifecycleFixture(); snapshot.run.segment = 4; snapshot.run.decision_count = 9
    const receipt = { source: 'ledger', action: 'create', request_id: 'request', audit_id: 'audit', created: false, snapshot }
    expect(parseLifecycleReceipt(receipt, 'create', 'request', createRunFixture).created).toBe(false)
    receipt.created = true
    expect(() => parseLifecycleReceipt(receipt, 'create', 'request', createRunFixture)).toThrow()
    receipt.created = false; snapshot.task_id = 'OTHER-TASK'
    expect(() => parseLifecycleReceipt(receipt, 'create', 'request', createRunFixture)).toThrow()
  })
  it('关闭必须保留段号和身份且响应绑定请求', () => {
    const before = lifecycleFixture(), after = lifecycleFixture()
    after.revision = 'lifecycle-2'; after.run.state = 'closed'; after.run.completed_at = '2026-11-19T13:00:00Z'; after.can_resume = false; after.can_close = false
    const receipt = { source: 'ledger', action: 'close', request_id: 'request', audit_id: 'audit', created: false, snapshot: after }
    expect(parseLifecycleReceipt(receipt, 'close', 'request', before).snapshot.run.segment).toBe(1)
    expect(() => parseLifecycleReceipt(receipt, 'close', 'OTHER', before)).toThrow()
    after.run.segment++
    expect(() => parseLifecycleReceipt(receipt, 'close', 'request', before)).toThrow()
  })
})
