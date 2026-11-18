import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import CoordBar from '../../src/components/CoordBar.vue'
import type { Report } from '../../src/api/types'
import preJson from '../../src/mock/pre.json'
import edgeJson from '../../src/mock/edge.json'

const pre = preJson as unknown as Report
const edge = edgeJson as unknown as Report

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('CoordBar', () => {
  it('正常态渲染：三段协调信息与完整态徽标', () => {
    wrapper = mount(CoordBar, {
      props: { coord: pre.coord, docState: pre.doc_state },
    })

    expect(wrapper.findAll('.coord__seg')).toHaveLength(3)
    expect(wrapper.findAll('.coord__seg dt').map((node) => node.text())).toEqual([
      'Skill Version',
      'Model',
      'Data Profile',
    ])
    expect(wrapper.findAll('.coord__value').map((node) => node.text())).toEqual([
      'assurance-skill@2.2.0',
      'deepseek-v4.1-flash',
      'retail-ops@2026-10',
    ])
    expect(wrapper.findAll('.coord__warn')).toHaveLength(0)
    expect(wrapper.find('.coord__tail').text()).toContain('数据完整')
    expect(wrapper.find('button').text()).toBe('导出 JSON')
  })

  it('缺失态：空 model 显示未指定与警示标记', () => {
    wrapper = mount(CoordBar, {
      props: { coord: edge.coord, docState: edge.doc_state },
    })

    expect(wrapper.findAll('.coord__seg.is-missing')).toHaveLength(1)
    expect(wrapper.find('.coord__seg.is-missing .coord__value').text()).toBe('未指定')
    expect(wrapper.findAll('.coord__warn')).toHaveLength(1)
    expect(wrapper.find('.coord__tail').text()).toContain('样本不足')
  })

  it('交互回调：导出按钮发出 action:export', async () => {
    wrapper = mount(CoordBar, {
      props: { coord: pre.coord, docState: pre.doc_state },
    })

    await wrapper.find('.coord__tail button').trigger('click')
    expect(wrapper.emitted('action:export')).toHaveLength(1)
  })

  it('键盘可达：导出按钮为原生可聚焦按钮', () => {
    wrapper = mount(CoordBar, {
      props: { coord: pre.coord, docState: pre.doc_state },
      attachTo: document.body,
    })

    const button = wrapper.find('.coord__tail button')
    expect(button.attributes('type')).toBe('button')
    expect(button.attributes('tabindex')).toBeUndefined()

    const element = button.element as HTMLButtonElement
    element.focus()
    expect(document.activeElement).toBe(element)
    expect(element.textContent).toBe('导出 JSON')
  })
})
