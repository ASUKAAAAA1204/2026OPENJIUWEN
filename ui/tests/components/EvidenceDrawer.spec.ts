import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import EvidenceDrawer from '../../src/components/EvidenceDrawer.vue'
import type { Claim } from '../../src/api/types'
import claimsJson from '../../src/mock/claims.json'

const locatorClaims = (claimsJson as unknown as Claim[]).filter((claim) => claim.dim === 'L')

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.style.overflow = ''
  document.body.innerHTML = ''
})

function mountDrawer(
  props: {
    open: boolean
    dimLabel: string
    dimNum: number
    dimDen: number
    dimState: 'VALUE' | 'NA' | 'MISSING'
    claims: Claim[]
  },
  attach = false,
) {
  return mount(EvidenceDrawer, {
    props,
    attachTo: attach ? document.body : undefined,
    global: { stubs: { teleport: true } },
  })
}

describe('EvidenceDrawer', () => {
  it('正常态渲染：VALUE 态列出来源结论与计数', async () => {
    wrapper = mountDrawer({
      open: true,
      dimLabel: '可定位',
      dimNum: 9,
      dimDen: 20,
      dimState: 'VALUE',
      claims: locatorClaims,
    })
    await nextTick()

    const dialog = wrapper.find('[role="dialog"]')
    expect(dialog.attributes('aria-modal')).toBe('true')
    expect(dialog.attributes('aria-label')).toBe('可定位 维度证据，9/20')
    expect(wrapper.find('.drawer__title').text()).toBe('可定位')
    expect(wrapper.find('.drawer__count').text()).toBe('9/20')
    expect(wrapper.findAll('.claim__head')).toHaveLength(20)
    expect(wrapper.find('.claim__status').text()).toContain('未定位')
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('缺失态：MISSING 无证据时显示未采集空态', async () => {
    wrapper = mountDrawer({
      open: true,
      dimLabel: '可复算',
      dimNum: 0,
      dimDen: 0,
      dimState: 'MISSING',
      claims: [],
    })
    await nextTick()

    expect(wrapper.find('.drawer__count').text()).toBe('未采集')
    expect(wrapper.find('.drawer__empty').text()).toBe('该维度尚未采集，暂无结论')
    expect(wrapper.find('.drawer__list').exists()).toBe(false)
    expect(wrapper.find('[role="dialog"]').attributes('aria-label')).toBe('可复算 维度证据，未采集')
  })

  it('交互回调：点击结论展开 aria 状态，关闭按钮发出 action:close', async () => {
    wrapper = mountDrawer({
      open: true,
      dimLabel: '可定位',
      dimNum: 9,
      dimDen: 20,
      dimState: 'VALUE',
      claims: locatorClaims,
    })
    await nextTick()

    const heads = wrapper.findAll('.claim__head')
    expect(heads[0].attributes('aria-expanded')).toBe('false')
    await heads[0].trigger('click')
    expect(heads[0].attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('.claim__detail').attributes('aria-hidden')).toBe('false')

    await heads[0].trigger('click')
    expect(heads[0].attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('.claim__detail').attributes('aria-hidden')).toBe('true')

    await wrapper.find('.drawer__head button').trigger('click')
    expect(wrapper.emitted('action:close')).toHaveLength(1)
  })

  it('键盘可达：Esc 关闭且 Tab 在面板内循环，卸载恢复 body 滚动', async () => {
    const drawer = mountDrawer(
      {
        open: true,
        dimLabel: '可定位',
        dimNum: 9,
        dimDen: 20,
        dimState: 'VALUE',
        claims: locatorClaims,
      },
      true,
    )
    wrapper = drawer
    await nextTick()

    const panel = wrapper.find('[role="dialog"]')
    expect(document.activeElement).toBe(panel.element)

    await panel.trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('action:close')).toHaveLength(1)

    const heads = wrapper.findAll('.claim__head')
    const last = heads[heads.length - 1].element as HTMLButtonElement
    last.focus()
    await panel.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(wrapper.find('.drawer__head button').element)

    drawer.unmount()
    wrapper = null
    expect(document.body.style.overflow).toBe('')
  })
})
