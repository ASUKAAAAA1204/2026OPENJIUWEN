import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import RunHistory from '../src/components/workbench/RunHistory.vue'
import { createPreview } from '../src/components/workbench/model'
const runs = [createPreview('RUN-1', '文档核查', 'openJiuwen'), createPreview('RUN-2', '代码检查', 'LangGraph')]
describe('运行历史', () => {
  it('组合筛选并显示空结果，清除后恢复记录', async () => {
    const wrapper = mount(RunHistory, { props: { runs, activeId: 'RUN-1' } })
    await wrapper.get('input').setValue('代码')
    expect(wrapper.findAll('.history-card')).toHaveLength(1)
    await wrapper.get('.history-filters select').setValue('openJiuwen')
    expect(wrapper.findAll('.history-card')).toHaveLength(0)
    expect(wrapper.text()).toContain('没有匹配')
    await wrapper.get('.workspace-empty button').trigger('click')
    expect(wrapper.findAll('.history-card')).toHaveLength(2)
  })
  it('选择记录传递原始运行对象', async () => {
    const wrapper = mount(RunHistory, { props: { runs, activeId: 'RUN-1' } })
    await wrapper.findAll('.history-card')[1].trigger('click')
    expect(wrapper.emitted('open')?.[0]).toEqual([runs[1]])
  })
})
