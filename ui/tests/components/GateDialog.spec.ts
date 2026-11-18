import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import GateDialog from '../../src/components/GateDialog.vue'
import type { Report } from '../../src/api/types'
import preJson from '../../src/mock/pre.json'
import partialJson from '../../src/mock/partial.json'

const pre = preJson as unknown as Report
const partial = partialJson as unknown as Report

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.style.overflow = ''
  document.body.innerHTML = ''
})

function mountGate(
  props: {
    decision: 'BLOCK' | 'PASS'
    reasonCode?: string
    score: number | null
    band: 'A' | 'B' | 'C' | 'D' | null
    threshold: number
    docState: 'DESIGN_ONLY' | 'PARTIAL' | 'COMPLETE'
    plan: Report['plan']
  },
  attach = false,
) {
  return mount(GateDialog, {
    props: { open: true, record: null, ...props },
    attachTo: attach ? document.body : undefined,
    global: { stubs: { teleport: true } },
  })
}

const blockProps = {
  decision: 'BLOCK' as const,
  reasonCode: 'ARS_BELOW_THRESHOLD',
  score: 47,
  band: 'D' as const,
  threshold: 70,
  docState: 'COMPLETE' as const,
  plan: pre.plan,
}

const partialProps = {
  decision: 'BLOCK' as const,
  reasonCode: 'SAMPLE_INSUFFICIENT',
  score: null,
  band: null,
  threshold: 70,
  docState: 'PARTIAL' as const,
  plan: partial.plan,
}

describe('GateDialog', () => {
  it('正常态渲染：阻断态标题、原因、报价与两个决策按钮', async () => {
    wrapper = mountGate(blockProps)
    await nextTick()

    expect(wrapper.find('.gate__title').text()).toBe('阻断发布')
    expect(wrapper.find('.gate__meta').text()).toBe('当前 47 分 · D 档 · 门槛 70 分')
    expect(wrapper.find('.gate__reason-text').text()).toBe('总审计就绪度低于发布门槛')
    expect(wrapper.find('.gate__code').text()).toBe('ARS_BELOW_THRESHOLD')
    expect(wrapper.find('.gate__seal-letter').text()).toBe('D')
    expect(wrapper.find('.gate__btn.btn--primary').text()).toBe('执行修复 → 79 分')
    expect(wrapper.find('.gate__btn.btn--primary').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.gate__btn--ack').text()).toBe('豁免发布')
    expect(wrapper.find('.gate__btn--ack').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('[role="dialog"]').attributes('aria-label')).toBe(
      '阻断发布，总审计就绪度低于发布门槛，当前 47 分 · D 档 · 门槛 70 分',
    )
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('缺失态：PARTIAL 无总分时修复与豁免均不可用', async () => {
    wrapper = mountGate(partialProps)
    await nextTick()

    expect(wrapper.find('.gate__title').text()).toBe('阻断发布')
    expect(wrapper.find('.gate__meta').text()).toBe('样本不足，未出总分 · 门槛 70 分')
    expect(wrapper.find('.gate__reason-text').text()).toBe('样本量不足，未出总分')
    expect(wrapper.find('.gate__btn.btn--primary').text()).toBe('修复不可用')
    expect(wrapper.find('.gate__btn.btn--primary').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.gate__btn--ack').text()).toBe('豁免不可用')
    expect(wrapper.find('.gate__btn--ack').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('.gate__figure--empty')).toHaveLength(2)
    expect(wrapper.find('.gate__figure--empty').text()).toBe('—')
  })

  it('交互回调：修复发出 action:repair，稍后处理二次确认后发出 action:close', async () => {
    wrapper = mountGate(blockProps)
    await nextTick()

    await wrapper.find('.gate__btn.btn--primary').trigger('click')
    expect(wrapper.emitted('action:repair')).toHaveLength(1)

    await wrapper.find('.gate__quiet').trigger('click')
    expect(wrapper.find('.gate__confirm-text').text()).toBe('离开将不记录本次决策')
    const confirmButtons = wrapper.findAll('.gate__confirm button')
    expect(confirmButtons).toHaveLength(2)
    await confirmButtons[1].trigger('click')
    expect(wrapper.emitted('action:close')).toHaveLength(1)
  })

  it('键盘可达：Esc 不关闭，Tab 在面板内循环，卸载恢复 body 滚动', async () => {
    const gate = mountGate(blockProps, true)
    wrapper = gate
    await nextTick()

    const panel = wrapper.find('[role="dialog"]')
    expect((document.activeElement?.textContent ?? '').trim()).toContain('执行修复')

    await panel.trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('action:close')).toBeUndefined()
    expect(wrapper.find('.gate__panel').exists()).toBe(true)

    const quiet = wrapper.find('.gate__quiet').element as HTMLButtonElement
    quiet.focus()
    await panel.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(wrapper.find('.gate__btn.btn--primary').element)

    gate.unmount()
    wrapper = null
    expect(document.body.style.overflow).toBe('')
  })
})
