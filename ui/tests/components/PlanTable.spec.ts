import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import PlanTable from '../../src/components/PlanTable.vue'
import type { Report } from '../../src/api/types'
import preJson from '../../src/mock/pre.json'
import partialJson from '../../src/mock/partial.json'
import designJson from '../../src/mock/design.json'

const pre = preJson as unknown as Report
const partial = partialJson as unknown as Report
const design = designJson as unknown as Report

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('PlanTable', () => {
  it('正常态渲染：按性价比降序的行、合计与预测', () => {
    wrapper = mount(PlanTable, { props: { plan: pre.plan } })

    expect(wrapper.find('.plan__meta').text()).toContain('按性价比降序 · 5 项')
    expect(wrapper.findAll('.plan__row')).toHaveLength(5)
    expect(wrapper.findAll('.plan__act').map((node) => node.text())).toEqual([
      '补定位',
      '重跑验证',
      '原地改写',
      '重验过期',
      '补字段',
    ])
    expect(wrapper.find('.plan__ratio').text()).toBe('1.79')
    expect(wrapper.find('.plan__total-cu').text()).toBe('34 CU')
    expect(wrapper.find('.plan__predicted').text()).toBe('79')
    expect(wrapper.find('.plan__predicted-unit').text()).toBe('分（B 档）')
    expect(wrapper.find('.plan__buffer').text()).toBe('含 5% 缓冲')
  })

  it('缺失态：无动作时显示空态且不出报价', () => {
    wrapper = mount(PlanTable, { props: { plan: partial.plan } })

    expect(wrapper.find('.plan__empty').text()).toBe('暂无方案 · 样本不足时不出报价')
    expect(wrapper.find('.plan__table').exists()).toBe(false)
    expect(wrapper.find('.plan__total').exists()).toBe(false)
  })

  it('状态切换：预测缺失时合计显示目标档与预测未出', async () => {
    wrapper = mount(PlanTable, { props: { plan: pre.plan } })
    expect(wrapper.find('.plan__predicted').exists()).toBe(true)

    await wrapper.setProps({ plan: design.plan })
    expect(wrapper.findAll('.plan__row')).toHaveLength(1)
    expect(wrapper.find('.plan__ratio').text()).toBe('0.67')
    expect(wrapper.find('.plan__predicted').exists()).toBe(false)
    expect(wrapper.find('.plan__pending').text()).toBe('目标 C 档 · 预测未出')
  })

  it('键盘可达替代：区域 aria-label 与表格 scope=col 提供读屏结构，无 Tab 停靠点', () => {
    wrapper = mount(PlanTable, { props: { plan: pre.plan } })

    expect(wrapper.find('section').attributes('aria-label')).toBe('改造方案，按性价比降序')
    const heads = wrapper.findAll('th')
    expect(heads).toHaveLength(5)
    expect(heads.map((head) => head.attributes('scope'))).toEqual(Array(5).fill('col'))
    expect(wrapper.findAll('button')).toHaveLength(0)
    expect(wrapper.findAll('[tabindex]')).toHaveLength(0)
  })
})
