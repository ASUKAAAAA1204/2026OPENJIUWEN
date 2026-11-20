import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import GovernanceWorkspace from '../src/components/workbench/GovernanceWorkspace.vue'
describe('治理工作区', () => {
  it('没有真实样本时八项读数均不产生数值', () => {
    const w = mount(GovernanceWorkspace, { props: { section: '版本评估', runs: [] } })
    expect(w.findAll('.metrics-grid strong').map(x => x.text())).toEqual(Array(8).fill('N/A'))
    expect(w.text()).toContain('样本不足')
  })
})
