import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import DeductionList from '../../src/components/DeductionList.vue'
import type { Report } from '../../src/api/types'
import preJson from '../../src/mock/pre.json'
import partialJson from '../../src/mock/partial.json'

const pre = preJson as unknown as Report
const partial = partialJson as unknown as Report

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('DeductionList', () => {
  it('正常态渲染：三条扣分项与价格元信息', () => {
    wrapper = mount(DeductionList, { props: { deductions: pre.deductions } })

    expect(wrapper.find('.ded__meta').text()).toBe('Deductions · 3 项')
    expect(wrapper.findAll('.ded__row')).toHaveLength(3)
    expect(wrapper.findAll('.ded__text').map((node) => node.text())).toEqual([
      '11 条结论只写了文件名、没有行号',
      '7 条规则无法编译，依赖字段缺失',
      '12 条结论无法在本机复算',
    ])
    expect(wrapper.findAll('.ded__action').map((node) => node.text())).toEqual([
      '补定位 9 条',
      '原地改写 4 条',
      '重跑验证 4 条',
    ])
    expect(wrapper.findAll('.ded__row-meta').map((node) => node.text())).toEqual([
      '补定位 9 条·6.3 CU',
      '原地改写 4 条·12 CU',
      '重跑验证 4 条·4 CU',
    ])
  })

  it('缺失态：无扣分项时显示空态', () => {
    wrapper = mount(DeductionList, { props: { deductions: partial.deductions } })

    expect(wrapper.find('.ded__empty').text()).toBe('暂无扣分项')
    expect(wrapper.find('.ded__list').exists()).toBe(false)
    expect(wrapper.find('.ded__meta').text()).toBe('Deductions · 0 项')
  })

  it('交互回调：点击行发出 action:select-deduction 维度键', async () => {
    wrapper = mount(DeductionList, { props: { deductions: pre.deductions } })

    await wrapper.findAll('.ded__row')[1].trigger('click')
    expect(wrapper.emitted('action:select-deduction')).toHaveLength(1)
    expect(wrapper.emitted('action:select-deduction')?.[0]).toEqual(['C'])
  })

  it('键盘可达：每行为原生按钮可 Tab 聚焦，Enter/Space 语义由按钮承担', () => {
    wrapper = mount(DeductionList, {
      props: { deductions: pre.deductions },
      attachTo: document.body,
    })

    const rows = wrapper.findAll('.ded__row')
    expect(rows.map((row) => row.attributes('type'))).toEqual(['button', 'button', 'button'])
    expect(rows.map((row) => row.attributes('disabled'))).toEqual([undefined, undefined, undefined])

    const first = rows[0].element as HTMLButtonElement
    first.focus()
    expect(document.activeElement).toBe(first)
  })
})
