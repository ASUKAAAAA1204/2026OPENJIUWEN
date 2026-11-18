import { onBeforeUnmount, ref, watch } from 'vue'

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * 数字滚动：rAF + easeOutCubic，可中断（新目标到来时取消上一帧）。
 * reduced-motion 下直接跳到终值；返回整数，避免小数闪烁。
 */
export function useTween(getTarget: () => number, duration = 600) {
  const display = ref(0)
  let raf = 0

  function cancel() {
    if (raf) cancelAnimationFrame(raf)
    raf = 0
  }

  function run(to: number) {
    cancel()
    const from = display.value
    if (prefersReducedMotion() || duration <= 0 || from === to) {
      display.value = to
      return
    }
    const start = performance.now()
    const step = (now: number) => {
      const p = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      display.value = Math.round(from + (to - from) * eased)
      if (p < 1) raf = requestAnimationFrame(step)
      else {
        display.value = to
        raf = 0
      }
    }
    raf = requestAnimationFrame(step)
  }

  watch(
    () => getTarget(),
    (value) => {
      if (!Number.isFinite(value)) {
        cancel()
        display.value = 0
        return
      }
      run(value)
    },
    { immediate: true },
  )

  onBeforeUnmount(cancel)

  return { display }
}
