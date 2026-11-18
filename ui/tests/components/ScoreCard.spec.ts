import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import ScoreCard from '../../src/components/ScoreCard.vue'
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

describe('ScoreCard', () => {
  it('正常态渲染：47 分 D 档、释义、计数与门槛', () => {
    wrapper = mount(ScoreCard, {
      props: {
        score: pre.score,
        band: pre.band,
        counts: pre.counts,
        docState: pre.doc_state,
        threshold: pre.gate.threshold,
      },
    })

    expect(wrapper.find('.score-card__num').text()).toBe('47')
    expect(wrapper.find('.seal__letter').text()).toBe('D')
    expect(wrapper.find('.score-card__caption').text()).toContain('D 档')
    expect(wrapper.find('.score-card__caption').text()).toContain('不可发布 · 缺口明显')
    expect(wrapper.find('.score-card__counts').text()).toBe('20 条结论 · 10 条规则 · 10 个检查')
    expect(wrapper.find('.score-card__threshold').text()).toBe('发布门槛 70')
    expect(wrapper.find('section').attributes('aria-label')).toBe(
      '审计就绪度 47 分，D 档，低于发布门槛 70',
    )
  })

  it('缺失态：PARTIAL 无总分时显示横线占位与不出总分文案', () => {
    wrapper = mount(ScoreCard, {
      props: {
        score: partial.score,
        band: partial.band,
        counts: partial.counts,
        docState: partial.doc_state,
        threshold: partial.gate.threshold,
      },
    })

    expect(wrapper.find('.score-card__num').exists()).toBe(false)
    expect(wrapper.find('.score-card__empty').exists()).toBe(true)
    expect(wrapper.find('.seal__letter').text()).toBe('—')
    expect(wrapper.find('.score-card__caption').text()).toContain('无档位')
    expect(wrapper.find('.score-card__caption').text()).toContain('样本不足，不出总分')
    expect(wrapper.find('.score-card__counts').text()).toBe('6 条结论 · 10 条规则 · 10 个检查')
    expect(wrapper.find('section').attributes('aria-label')).toBe(
      '审计就绪度 无数据，样本不足，不可发布决策',
    )
  })

  it('状态切换：props 更新后读数、档位与 aria 结论同步刷新', async () => {
    wrapper = mount(ScoreCard, {
      props: {
        score: pre.score,
        band: pre.band,
        counts: pre.counts,
        docState: pre.doc_state,
        threshold: pre.gate.threshold,
      },
    })

    await wrapper.setProps({ score: 79, band: 'B' })
    expect(wrapper.find('.score-card__num').text()).toBe('79')
    expect(wrapper.find('.seal__letter').text()).toBe('B')
    expect(wrapper.find('.score-card__caption').text()).toContain('B 档')
    expect(wrapper.find('.score-card__caption').text()).toContain('可用 · 满足发布门槛')
    expect(wrapper.find('section').attributes('aria-label')).toBe(
      '审计就绪度 79 分，B 档，不低于发布门槛 70',
    )
  })

  it('键盘可达替代：容器 role=group 携带完整 aria 结论，无 Tab 停靠点', () => {
    wrapper = mount(ScoreCard, {
      props: {
        score: pre.score,
        band: pre.band,
        counts: pre.counts,
        docState: pre.doc_state,
        threshold: 80,
      },
    })

    expect(wrapper.find('section').attributes('role')).toBe('group')
    expect(wrapper.find('section').attributes('tabindex')).toBeUndefined()
    expect(wrapper.find('.score-card__threshold').text()).toBe('发布门槛 80')
    expect(wrapper.find('section').attributes('aria-label')).toBe(
      '审计就绪度 47 分，D 档，低于发布门槛 80',
    )
    expect(wrapper.findAll('button')).toHaveLength(0)
  })
})
