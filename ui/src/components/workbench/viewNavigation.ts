/** Roving focus for mutually exclusive view buttons; selection follows keyboard focus. */
export function navigateView(event: KeyboardEvent, values: readonly string[], current: string, select: (value: string) => void) {
  const index = values.indexOf(current)
  if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? values.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + values.length) % values.length
  select(values[next])
  const group = (event.currentTarget as HTMLElement).parentElement
  group?.querySelectorAll<HTMLButtonElement>('button')[next]?.focus()
}
