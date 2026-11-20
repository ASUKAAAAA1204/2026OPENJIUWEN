import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ApprovalWorkspace from '../src/components/workbench/ApprovalWorkspace.vue'
describe('演示审批', () => {
  it('核对前阻止批准，核对后完成且不能重复处理', async () => {
    const w = mount(ApprovalWorkspace)
    await w.get('.primary').trigger('click')
    expect(w.get('[role=alert]').text()).toContain('请先确认')
    await w.get('input[type=checkbox]').setValue(true)
    await w.get('.primary').trigger('click')
    expect(w.get('.approval-result').text()).toContain('演示已批准')
    expect(w.find('.approval-actions').exists()).toBe(false)
  })
  it('拒绝必须说明理由', async () => {
    const w = mount(ApprovalWorkspace)
    await w.get('input[type=checkbox]').setValue(true)
    await w.get('.reject-button').trigger('click')
    expect(w.get('[role=alert]').text()).toContain('填写原因')
    await w.get('textarea').setValue('目标分支未经确认')
    await w.get('.reject-button').trigger('click')
    expect(w.get('.approval-result').text()).toContain('目标分支未经确认')
    expect(w.text()).toContain('演示已拒绝')
  })
})
