import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import SegmentBar from '../../src/components/SegmentBar.vue'

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('SegmentBar', () => {
  it('正常态渲染：VALUE 显示填充条并写入比例', () => {
    wrapper = mount(SegmentBar, { props: { num: 9, den: 20, state: 'VALUE' } })

    expect(wrapper.classes()).toContain('segbar--value')
    expect(wrapper.find('.segbar__fill').exists()).toBe(true)
    expect(wrapper.find('.segbar__badge').exists()).toBe(false)
    expect(wrapper.attributes('style')).toContain('--ratio: 0.45')
  })

  it('缺失态：MISSING 只画斜纹轨道，不显示填充', () => {
    wrapper = mount(SegmentBar, { props: { num: 0, den: 0, state: 'MISSING' } })

    expect(wrapper.classes()).toContain('segbar--missing')
    expect(wrapper.find('.segbar__fill').exists()).toBe(false)
    expect(wrapper.attributes('style')).toContain('--ratio: 0')
  })

  it('比例响应：den 不变时 num 更新重算比例', async () => {
    wrapper = mount(SegmentBar, { props: { num: 3, den: 20, state: 'VALUE' } })
    expect(wrapper.attributes('style')).toContain('--ratio: 0.15')

    await wrapper.setProps({ num: 18 })
    expect(wrapper.attributes('style')).toContain('--ratio: 0.9')
  })

  it('aria 状态：装饰条对读屏隐藏，徽标文本可读且无 Tab 停靠点', () => {
    wrapper = mount(SegmentBar, {
      props: { num: 6, den: 10, state: 'VALUE', badge: 'n=6' },
    })

    expect(wrapper.attributes('aria-hidden')).toBe('true')
    expect(wrapper.find('.segbar__badge').text()).toBe('n=6')
    expect(wrapper.findAll('button')).toHaveLength(0)
    expect(wrapper.findAll('[tabindex]')).toHaveLength(0)
  })
})
