import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import DimensionBars from '../../src/components/DimensionBars.vue'
import type { Report } from '../../src/api/types'
import preJson from '../../src/mock/pre.json'
import designJson from '../../src/mock/design.json'
import partialJson from '../../src/mock/partial.json'

const pre = preJson as unknown as Report
const design = designJson as unknown as Report
const partial = partialJson as unknown as Report

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('DimensionBars', () => {
  it('正常态渲染：五维名称、计数、权重与分段条', () => {
    wrapper = mount(DimensionBars, { props: { dims: pre.dims, clickable: true } })

    expect(wrapper.find('.dims__title').text()).toBe('五维分项')
    expect(wrapper.findAll('.dim')).toHaveLength(5)
    expect(wrapper.findAll('.dim__name').map((node) => node.text())).toEqual([
      '① 可定位',
      '② 可编译',
      '③ 可复算',
      '④ 确定性',
      '⑤ 新鲜度',
    ])
    expect(wrapper.findAll('.dim__count').map((node) => node.text())).toEqual([
      '9/20',
      '3/10',
      '8/20',
      '6/10',
      '15/20',
    ])
    expect(wrapper.findAll('.dim__weight').map((node) => node.text())).toEqual([
      '25%',
      '25%',
      '20%',
      '15%',
      '15%',
    ])
    expect(wrapper.findAll('.segbar')).toHaveLength(5)
  })

  it('缺失态：NA 与 MISSING 降饱和并给出对应文案', () => {
    wrapper = mount(DimensionBars, { props: { dims: design.dims, clickable: true } })

    expect(wrapper.findAll('.dim__count').map((node) => node.text())).toEqual([
      '无适用项',
      '3/10',
      '未采集',
      '未采集',
      '未采集',
    ])
    expect(wrapper.findAll('.dim.is-quiet')).toHaveLength(4)
    expect(wrapper.findAll('.segbar--na')).toHaveLength(1)
    expect(wrapper.findAll('.segbar--missing')).toHaveLength(3)
    expect(wrapper.findAll('.segbar--value')).toHaveLength(1)
  })

  it('交互回调：clickable 时发出 action:select-dim，不可点时禁用且不发', async () => {
    wrapper = mount(DimensionBars, { props: { dims: pre.dims, clickable: true } })
    await wrapper.findAll('.dim')[0].trigger('click')
    expect(wrapper.emitted('action:select-dim')).toHaveLength(1)
    expect(wrapper.emitted('action:select-dim')?.[0]).toEqual(['L'])

    wrapper.unmount()
    wrapper = mount(DimensionBars, { props: { dims: pre.dims, clickable: false } })
    expect(wrapper.findAll('.dim')[0].attributes('disabled')).toBeDefined()
    await wrapper.findAll('.dim')[0].trigger('click')
    expect(wrapper.emitted('action:select-dim')).toBeUndefined()
  })

  it('键盘可达：clickable 时按钮可聚焦，关闭后退出 Tab 序', async () => {
    wrapper = mount(DimensionBars, {
      props: { dims: partial.dims, clickable: true },
      attachTo: document.body,
    })

    const first = wrapper.findAll('.dim')[0]
    const element = first.element as HTMLButtonElement
    element.focus()
    expect(document.activeElement).toBe(element)
    expect(first.attributes('disabled')).toBeUndefined()
    // 样本不足 n=6 时条尾有徽标
    expect(wrapper.find('.segbar__badge').text()).toBe('n=6')

    await wrapper.setProps({ clickable: false })
    await nextTick()
    expect(wrapper.findAll('.dim').map((dim) => dim.attributes('disabled'))).toEqual(
      Array(5).fill(''),
    )
  })
})
