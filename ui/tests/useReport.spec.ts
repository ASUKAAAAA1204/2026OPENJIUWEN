import { createApp, defineComponent, h, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Report } from '../src/api/types'

vi.mock('../src/api/client', () => ({
  resolveSource: vi.fn(() => ({ mode: 'mock', scenario: 'pre' })),
  getReport: vi.fn(),
}))

import { getReport } from '../src/api/client'
import { useReport } from '../src/composables/useReport'

const cleanups: Array<() => void> = []

function mountReport() {
  let state!: ReturnType<typeof useReport>
  const Comp = defineComponent({
    setup() {
      state = useReport()
      return () => h('div', state.loading.value ? 'loading' : state.error.value ?? 'ok')
    },
  })
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(Comp)
  app.mount(host)
  cleanups.push(() => {
    app.unmount()
    host.remove()
  })
  return { state, host }
}

async function flush() {
  await new Promise((r) => setTimeout(r, 0))
  await nextTick()
}

beforeEach(() => {
  vi.mocked(getReport).mockReset()
})

afterEach(() => {
  cleanups.splice(0).forEach((fn) => fn())
})

describe('useReport', () => {
  it('挂载后加载 mock 场景', async () => {
    const report = { score: 47, band: 'D' } as Report
    vi.mocked(getReport).mockResolvedValue(report)
    const { state, host } = mountReport()
    expect(state.loading.value).toBe(true)
    await flush()
    expect(state.loading.value).toBe(false)
    expect(state.report.value?.score).toBe(47)
    expect(state.error.value).toBeNull()
    expect(host.textContent).toBe('ok')
  })

  it('加载失败写入错误文案', async () => {
    vi.mocked(getReport).mockRejectedValue(new Error('网络错误'))
    const { state } = mountReport()
    await flush()
    expect(state.loading.value).toBe(false)
    expect(state.report.value).toBeNull()
    expect(state.error.value).toBe('网络错误')
  })

  it('load 切换场景后重新取数', async () => {
    vi.mocked(getReport).mockResolvedValue({ score: 79, band: 'B' } as Report)
    const { state } = mountReport()
    await flush()
    await state.load({ mode: 'mock', scenario: 'post' })
    expect(getReport).toHaveBeenLastCalledWith({ mode: 'mock', scenario: 'post' })
    expect(state.report.value?.score).toBe(79)
  })
})
