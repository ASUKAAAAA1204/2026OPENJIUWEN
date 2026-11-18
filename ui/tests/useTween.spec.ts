import { createApp, defineComponent, h, nextTick, ref, type Ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useTween } from '../src/composables/useTween'

const cleanups: Array<() => void> = []

function mountTween(target: () => number, duration = 600) {
  let display!: Ref<number>
  const Comp = defineComponent({
    setup() {
      const result = useTween(target, duration)
      display = result.display
      return () => h('span', String(result.display.value))
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
  return { display, host }
}

function fakeMedia(matches: boolean): MediaQueryList {
  return {
    matches,
    media: '',
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  } as unknown as MediaQueryList
}

afterEach(() => {
  cleanups.splice(0).forEach((fn) => fn())
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('useTween', () => {
  it('reduced-motion 直接跳到终值', async () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue(fakeMedia(true))
    const { display, host } = mountTween(() => 79)
    await nextTick()
    expect(display.value).toBe(79)
    expect(host.textContent).toBe('79')
  })

  it('非有限目标归零，不渲染 NaN', async () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue(fakeMedia(true))
    const { display } = mountTween(() => Number.NaN)
    await nextTick()
    expect(display.value).toBe(0)
  })

  it('600ms 补间结束时落在整数终值', async () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue(fakeMedia(false))
    let frame: FrameRequestCallback | null = null
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      frame = cb
      return 1
    })
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    vi.spyOn(performance, 'now').mockReturnValue(0)

    const { display } = mountTween(() => 79)
    await nextTick()
    expect(frame).not.toBeNull()
    frame!(600)
    expect(display.value).toBe(79)
  })

  it('目标变化时取消上一帧再补间', async () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue(fakeMedia(false))
    const cancel = vi.fn()
    vi.stubGlobal('requestAnimationFrame', () => 1)
    vi.stubGlobal('cancelAnimationFrame', cancel)

    const target = ref(47)
    const { display } = mountTween(() => target.value)
    await nextTick()
    target.value = 79
    await nextTick()
    expect(cancel).toHaveBeenCalled()
    // 帧回调未执行，数值仍停在起点
    expect(display.value).toBe(0)
  })
})
