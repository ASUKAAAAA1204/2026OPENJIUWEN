import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import StatusBadge from '../../src/components/StatusBadge.vue'

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('StatusBadge', () => {
  it('正常态渲染：COMPLETE 为勾选图标与数据完整', () => {
    wrapper = mount(StatusBadge, { props: { state: 'COMPLETE' } })

    expect(wrapper.text()).toBe('数据完整')
    expect(wrapper.classes()).toContain('badge--ok')
    expect(wrapper.find('path').attributes('d')).toBe('M2 6.4 4.6 9 10 3.4')
  })

  it('缺失态：PARTIAL 降饱和并显示样本不足', () => {
    wrapper = mount(StatusBadge, { props: { state: 'PARTIAL' } })

    expect(wrapper.text()).toBe('样本不足')
    expect(wrapper.classes()).toContain('badge--warn')
    expect(wrapper.classes()).toContain('badge--dim')
    expect(wrapper.findAll('rect')).toHaveLength(2)
  })

  it('状态切换：DESIGN_ONLY 切换为虚线方框与仅设计态', async () => {
    wrapper = mount(StatusBadge, { props: { state: 'COMPLETE' } })

    await wrapper.setProps({ state: 'DESIGN_ONLY' })
    expect(wrapper.text()).toBe('仅设计态')
    expect(wrapper.classes()).toContain('badge--muted')
    expect(wrapper.find('rect').attributes('stroke-dasharray')).toBe('2 2')
  })

  it('aria 状态：图标对读屏隐藏，文本承载状态且无 Tab 停靠点', () => {
    wrapper = mount(StatusBadge, { props: { state: 'COMPLETE' } })

    expect(wrapper.attributes('aria-hidden')).toBeUndefined()
    expect(wrapper.find('svg').attributes('aria-hidden')).toBe('true')
    expect(wrapper.find('span').text()).toBe('数据完整')
    expect(wrapper.attributes('tabindex')).toBeUndefined()
    expect(wrapper.findAll('button')).toHaveLength(0)
  })
})
