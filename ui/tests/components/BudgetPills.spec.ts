import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import BudgetPills from '../../src/components/BudgetPills.vue'
import type { Report } from '../../src/api/types'
import preJson from '../../src/mock/pre.json'
import partialJson from '../../src/mock/partial.json'

const pre = preJson as unknown as Report
const partial = partialJson as unknown as Report

let wrapper: VueWrapper | null = null

beforeEach(() => {
  vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('BudgetPills', () => {
  it('正常态渲染：五档预算、末档选中与预测读数', () => {
    wrapper = mount(BudgetPills, {
      props: { plan: pre.plan, activeCu: null },
    })

    const pills = wrapper.findAll('.budget__pill')
    expect(pills).toHaveLength(5)
    expect(pills.map((pill) => pill.text())).toEqual(['0', '8', '16', '24', '34'])
    expect(pills.map((pill) => pill.attributes('aria-label'))).toEqual([
      '0 CU',
      '8 CU',
      '16 CU',
      '24 CU',
      '34 CU',
    ])
    expect(pills[4].attributes('aria-pressed')).toBe('true')
    expect(pills[0].attributes('aria-pressed')).toBe('false')
    expect(wrapper.findAll('.budget__pill.is-active')).toHaveLength(1)
    expect(wrapper.find('.budget__predicted').text()).toBe('79')
    expect(wrapper.find('.budget__target').text()).toBe('目标 B 档')
  })

  it('缺失态：无预算档位时显示预测未出', () => {
    wrapper = mount(BudgetPills, {
      props: { plan: partial.plan, activeCu: null },
    })

    expect(wrapper.find('.budget__empty').text()).toBe('暂无预算档位 · 预测未出')
    expect(wrapper.find('.budget__pills').exists()).toBe(false)
    expect(wrapper.find('.budget__readout').exists()).toBe(false)
  })

  it('交互回调：点击非当前档发出 action:select-budget，点击当前档不重复发出', async () => {
    wrapper = mount(BudgetPills, {
      props: { plan: pre.plan, activeCu: null },
    })

    const pills = wrapper.findAll('.budget__pill')
    await pills[1].trigger('click')
    expect(wrapper.emitted('action:select-budget')).toHaveLength(1)
    expect(wrapper.emitted('action:select-budget')?.[0]).toEqual([8])

    await pills[4].trigger('click')
    expect(wrapper.emitted('action:select-budget')).toHaveLength(1)
  })

  it('键盘可达：档位为原生按钮可 Tab 聚焦，aria-pressed 暴露选中态', () => {
    wrapper = mount(BudgetPills, {
      props: { plan: pre.plan, activeCu: 8 },
      attachTo: document.body,
    })

    const pills = wrapper.findAll('.budget__pill')
    expect(pills.map((pill) => pill.attributes('type'))).toEqual(Array(5).fill('button'))
    expect(pills.map((pill) => pill.attributes('tabindex'))).toEqual(Array(5).fill(undefined))
    expect(pills[1].attributes('aria-pressed')).toBe('true')
    expect(pills[0].attributes('aria-pressed')).toBe('false')

    const target = pills[0].element as HTMLButtonElement
    target.focus()
    expect(document.activeElement).toBe(target)
  })
})
