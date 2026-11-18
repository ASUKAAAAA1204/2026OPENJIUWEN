import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import CostCurve from '../../src/components/CostCurve.vue'
import type { Report } from '../../src/api/types'
import preJson from '../../src/mock/pre.json'
import partialJson from '../../src/mock/partial.json'

const pre = preJson as unknown as Report
const partial = partialJson as unknown as Report

vi.mock('uplot', () => {
  interface FakeOpts {
    width: number
    height: number
    scales: {
      x: { range: [number, number] }
      y: { range: [number, number] }
    }
    hooks: { ready?: Array<(chart: FakeChart) => void> }
  }

  class FakeChart {
    width: number
    height: number
    ctx = { canvas: { width: 0 } }
    bbox = { left: 32, top: 8, width: 268, height: 160 }
    over: HTMLDivElement
    opts: FakeOpts
    data: number[][]

    constructor(opts: FakeOpts, data: number[][], target: HTMLElement) {
      this.opts = opts
      this.data = data
      this.width = opts.width
      this.height = opts.height
      this.ctx.canvas.width = opts.width
      this.over = document.createElement('div')
      this.over.getBoundingClientRect = () => ({
        left: 0,
        top: 0,
        right: opts.width,
        bottom: opts.height,
        width: opts.width,
        height: opts.height,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      })
      target.appendChild(this.over)
      // 真实 uPlot 首次 commit 后才触发 ready；这里异步模拟，让 chart 变量已赋值
      setTimeout(() => {
        this.opts.hooks.ready?.forEach((fn) => fn(this))
      }, 0)
    }

    valToPos(val: number, scale: 'x' | 'y'): number {
      if (scale === 'x') return this.xPos(val)
      const [min, max] = this.opts.scales.y.range
      return this.bbox.top + this.bbox.height - ((val - min) / (max - min || 1)) * this.bbox.height
    }

    posToIdx(x: number): number {
      let index = 0
      let distance = Infinity
      this.data[0].forEach((cu, i) => {
        const next = Math.abs(this.xPos(cu) - x)
        if (next < distance) {
          distance = next
          index = i
        }
      })
      return index
    }

    redraw() {}

    setSize(size: { width: number; height: number }) {
      this.width = size.width
      this.height = size.height
      this.ctx.canvas.width = size.width
    }

    destroy() {}

    private xPos(cu: number): number {
      const [, max] = this.opts.scales.x.range
      return this.bbox.left + (cu / (max || 1)) * this.bbox.width
    }
  }

  return { default: FakeChart }
})

let wrapper: VueWrapper | null = null

beforeEach(() => {
  vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
}

const curveProps = {
  trend: pre.trend,
  threshold: pre.gate.threshold,
  marginalFrom: pre.marginal_exhausted_from,
  tiers: pre.plan.tiers,
  activeCu: null,
}

describe('CostCurve', () => {
  it('正常态渲染：摘要、门槛线与六个点标注', async () => {
    wrapper = mount(CostCurve, { props: curveProps })
    await settle()

    expect(wrapper.find('.curve__summary').text()).toBe('从 47 分到 79 分 · 花费 34 CU')
    expect(wrapper.find('.curve__threshold-label').text()).toBe('发布门槛 70')
    expect(wrapper.findAll('.curve__pt')).toHaveLength(6)
    expect(wrapper.find('.curve__tier').exists()).toBe(true)
    expect(wrapper.find('.curve__marginal-label').text()).toBe('边际枯竭')
  })

  it('缺失态：趋势点不足两条时显示暂无曲线', () => {
    wrapper = mount(CostCurve, { props: { ...curveProps, trend: partial.trend } })

    expect(wrapper.find('.curve__empty').text()).toBe('暂无提升曲线 · 预测未出')
    expect(wrapper.find('.curve__summary').exists()).toBe(false)
    expect(wrapper.find('.curve__plot').exists()).toBe(false)
  })

  it('交互等价（无 emit）：指针移动显示行内读数', async () => {
    wrapper = mount(CostCurve, { props: curveProps, attachTo: document.body })
    await settle()

    const over = wrapper.find('.curve__plot').element.querySelector('div')
    expect(over).not.toBeNull()
    const event = new Event('pointermove')
    Object.defineProperty(event, 'clientX', { value: 32 })
    over?.dispatchEvent(event)
    await nextTick()

    expect(wrapper.find('.curve__cross-read').text()).toBe('0 CU · 47 分')
  })

  it('键盘可达替代：图表以 role=img 与 aria-label 暴露完整读数，无 Tab 停靠点', async () => {
    wrapper = mount(CostCurve, { props: curveProps })
    await settle()

    expect(wrapper.find('.curve__plot').attributes('role')).toBe('img')
    expect(wrapper.find('.curve__plot').attributes('aria-label')).toBe(
      '提升曲线：横轴累计 CU，纵轴 ARS；从 47 分到 79 分，共 34 CU，发布门槛 70 分',
    )
    expect(wrapper.findAll('button')).toHaveLength(0)
    expect(wrapper.findAll('[tabindex]')).toHaveLength(0)
  })
})
