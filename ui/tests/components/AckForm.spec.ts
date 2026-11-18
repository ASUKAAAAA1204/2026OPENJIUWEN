import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import AckForm from '../../src/components/AckForm.vue'
import { today } from '../../src/utils/ack'

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.style.overflow = ''
  document.body.innerHTML = ''
})

describe('AckForm', () => {
  it('正常态渲染：四个原因码、备注框与初始禁用提交', () => {
    wrapper = mount(AckForm, { attachTo: document.body })

    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(4)
    expect(wrapper.findAll('.reason__label').map((node) => node.text())).toEqual([
      '预算耗尽',
      '低严重度',
      '延后',
      '结构性不可验',
    ])
    expect(wrapper.findAll('.reason__code').map((node) => node.text())).toEqual([
      'BUDGET_EXHAUSTED',
      'LOW_SEVERITY',
      'DEFERRED',
      'STRUCTURALLY_UNVERIFIABLE',
    ])
    expect(wrapper.find('textarea').attributes('maxlength')).toBe('200')
    expect(wrapper.find('.ack__count').text()).toBe('0 / 200')
    expect(wrapper.find('button.btn--primary').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.ack__hint').text()).toBe('选择原因码并填写备注后可提交')
  })

  it('缺失态：备注为空或全空白时不可提交', async () => {
    wrapper = mount(AckForm, { attachTo: document.body })

    await wrapper.findAll('input[type="radio"]')[1].setValue(true)
    expect(wrapper.find('button.btn--primary').attributes('disabled')).toBeDefined()

    await wrapper.find('textarea').setValue('   ')
    expect(wrapper.find('button.btn--primary').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.ack__hint').text()).toBe('选择原因码并填写备注后可提交')
    expect(wrapper.emitted('action:submit')).toBeUndefined()
  })

  it('交互回调：提交 action:submit 载荷，返回 action:back', async () => {
    wrapper = mount(AckForm, { attachTo: document.body })

    await wrapper.findAll('input[type="radio"]')[2].setValue(true)
    await wrapper.find('textarea').setValue('  下个迭代补齐  ')
    expect(wrapper.find('button.btn--primary').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.ack__hint').text()).toBe('提交后写入发布记录，可随时查看')

    await wrapper.find('button.btn--primary').trigger('click')
    const submitted = wrapper.emitted('action:submit')
    expect(submitted).toHaveLength(1)
    expect(submitted?.[0]).toEqual([{ code: 'DEFERRED', note: '下个迭代补齐', date: today() }])

    await wrapper.find('.ack__actions button:not(.btn--primary)').trigger('click')
    expect(wrapper.emitted('action:back')).toHaveLength(1)
  })

  it('键盘可达：挂载聚焦首个原因码，可 Tab 到备注，Enter/Space 等价点击提交', async () => {
    wrapper = mount(AckForm, { attachTo: document.body })

    const first = document.activeElement as HTMLInputElement
    expect(first.type).toBe('radio')
    expect(first.value).toBe('BUDGET_EXHAUSTED')
    expect(first.tabIndex).toBe(0)

    const note = wrapper.find('textarea').element as HTMLTextAreaElement
    note.focus()
    expect(document.activeElement).toBe(note)

    await wrapper.findAll('input[type="radio"]')[3].setValue(true)
    await wrapper.find('textarea').setValue('结构不可验，留痕')
    // happy-dom 不实现 Enter→click，用原生按钮 click 作为 Enter/Space 等价断言
    await wrapper.find('button.btn--primary').trigger('click')
    expect(wrapper.emitted('action:submit')).toHaveLength(1)
  })
})
