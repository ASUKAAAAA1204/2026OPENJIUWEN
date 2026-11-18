import { onMounted, ref } from 'vue'
import type { Report } from '../api/types'
import { getReport, resolveSource, type ReportSource } from '../api/client'

/** 单一数据源：场景切换只换 report，组件不变 */
export function useReport() {
  const source = ref<ReportSource>(resolveSource(window.location.search))
  const report = ref<Report | null>(null)
  const loading = ref(true)
  const error = ref<string | null>(null)

  async function load(next: ReportSource) {
    source.value = next
    loading.value = true
    error.value = null
    try {
      report.value = await getReport(next)
    } catch (e) {
      error.value = e instanceof Error ? e.message : '数据加载失败'
    } finally {
      loading.value = false
    }
  }

  onMounted(() => {
    void load(source.value)
  })

  return { source, report, loading, error, load }
}
