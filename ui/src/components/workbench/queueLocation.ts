import { onMounted, onUnmounted, watch, type Ref } from 'vue'
type Queue = '人工审批' | '证据与债务'
const filters = { 人工审批: ['待处理', '已处理', '全部'], 证据与债务: ['未消解', '已消解', '全部'] }
export function readQueueLocation(hash: string, section: Queue) {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  if (params.get('section') !== section) return null
  const source = params.get('queue_source'), filter = params.get('queue_filter'), id = params.get('queue_item') ?? ''
  return {
    source: source === 'api' || source === 'preview' ? source as 'api' | 'preview' : null,
    filter: filters[section].includes(filter ?? '') ? filter! : filters[section][0]!,
    selected: id === id.trim() && id.length <= 512 ? id : '',
  }
}
export function useQueueLocation(section: Queue, mode: Ref<'api' | 'preview'>, filter: Ref<string>, selected: Ref<string>, busy: Ref<boolean>) {
  function restore() {
    if (busy.value) return
    const value = readQueueLocation(location.hash, section)
    if (!value) return
    mode.value = 'api'
    filter.value = value.filter
    selected.value = value.selected
  }
  function save() {
    if (new URLSearchParams(location.hash.slice(1)).get('section') !== section) return
    const params = new URLSearchParams({ section, queue_source: mode.value, queue_filter: filter.value })
    if (selected.value) params.set('queue_item', selected.value)
    history.replaceState(null, '', `${location.pathname}${location.search}#${params}`)
  }
  restore()
  watch([mode, filter, selected], save, { flush: 'post' })
  onMounted(() => { save(); window.addEventListener('popstate', restore); window.addEventListener('workspace-location', restore) })
  onUnmounted(() => { window.removeEventListener('popstate', restore); window.removeEventListener('workspace-location', restore) })
}
