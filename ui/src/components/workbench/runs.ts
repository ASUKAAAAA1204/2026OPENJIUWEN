import { onScopeDispose, ref, shallowRef } from 'vue'
import type { Framework, RunPreview } from './model'
export interface RunSummary extends RunPreview {
  source: 'ledger'; state: 'open' | 'closed' | 'degraded'; segment: number
  started_at: string; completed_at: string | null; parent_run_id: string | null; child_run_ids: string[]; decision_count: number
}
export interface RunPage { source: 'ledger'; items: RunSummary[]; next_cursor: string | null }
const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
const time = (value: unknown) => text(value) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))
export function parseRunPage(value: unknown): RunPage {
  const fail = () => { throw new Error('运行列表响应不符合生命周期或分页契约，请重新读取。') }
  if (typeof value !== 'object' || value === null) return fail()
  const page = value as Record<string, unknown>
  if (page.source !== 'ledger' || !Array.isArray(page.items) || !(page.next_cursor === null || text(page.next_cursor))) return fail()
  const ids = new Set<string>()
  for (const raw of page.items) {
    if (typeof raw !== 'object' || raw === null) return fail()
    const item = raw as Record<string, unknown>
    if (!text(item.id) || !text(item.title) || ids.has(item.id) || item.source !== 'ledger' || !['openJiuwen', 'LangGraph', 'OpenAI Agents SDK'].includes(String(item.framework)) || !['open', 'closed', 'degraded'].includes(String(item.state)) || !Number.isSafeInteger(item.segment) || (item.segment as number) < 1 || !time(item.started_at) || !Number.isSafeInteger(item.decision_count) || (item.decision_count as number) < 0 || !(item.parent_run_id === null || text(item.parent_run_id)) || item.parent_run_id === item.id || !Array.isArray(item.child_run_ids) || !item.child_run_ids.every(text) || new Set(item.child_run_ids).size !== item.child_run_ids.length || item.child_run_ids.includes(item.id) || (item.parent_run_id !== null && item.child_run_ids.includes(item.parent_run_id)) || !Array.isArray(item.tools) || item.tools.length !== 0) return fail()
    if (item.state === 'closed' ? !time(item.completed_at) || Date.parse(String(item.completed_at)) < Date.parse(String(item.started_at)) : item.completed_at !== null) return fail()
    ids.add(item.id)
  }
  if (page.next_cursor !== null && page.items.length === 0) return fail()
  return page as unknown as RunPage
}
export function relatedRun(id: string, framework: Framework): RunPreview { return { id, title: `关联运行 ${id}`, framework, tools: [], source: 'ledger' } }
export function useRuns() {
  const items = shallowRef<RunSummary[]>([]), loading = ref(false), error = ref(''), nextCursor = ref<string | null>(null)
  const query = ref(''), framework = ref('全部框架'), state = ref('全部状态')
  let revision = 0, controller: AbortController | undefined
  const cursors = new Set<string>()
  let applied = new URLSearchParams()
  async function load(more = false) {
    if (more && (loading.value || !nextCursor.value)) return
    const token = ++revision, cursor = more ? nextCursor.value : null
    controller?.abort(); controller = new AbortController()
    const active = controller, timer = setTimeout(() => active.abort(), 12000)
    if (!more) { items.value = []; nextCursor.value = null; cursors.clear() }
    loading.value = true; error.value = ''
    if (!more) {
      applied = new URLSearchParams({ q: query.value.trim() })
      if (framework.value !== '全部框架') applied.set('framework', framework.value)
      if (state.value !== '全部状态') applied.set('state', state.value)
    }
    const params = new URLSearchParams(applied)
    if (cursor) params.set('cursor', cursor)
    try {
      const response = await fetch(`/api/v27/runs?${params}`, { signal: active.signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      if (token !== revision) return
      if (response.status === 401 || response.status === 403) { items.value = []; nextCursor.value = null; cursors.clear() }
      if (!response.ok) throw new Error(({ 401: '登录已失效，请重新登录。', 403: '当前账号无权查看运行列表。', 404: '运行查询接口尚未提供。' } as Record<number, string>)[response.status] ?? '运行服务暂时不可用，请重试。')
      const result = parseRunPage(await response.json())
      if (token !== revision) return
      const known = new Set(items.value.map(item => item.id))
      if ((more && result.items.some(item => known.has(item.id))) || (result.next_cursor && (result.next_cursor === cursor || cursors.has(result.next_cursor)))) throw new Error('分页游标或运行记录重复，请重新查询。')
      if (cursor) cursors.add(cursor)
      items.value = more ? [...items.value, ...result.items] : result.items; nextCursor.value = result.next_cursor
    } catch (cause) { if (token === revision) error.value = active.signal.aborted ? '运行查询超时，请重试。' : cause instanceof Error ? cause.message : '无法读取运行列表。' }
    finally { clearTimeout(timer); if (token === revision) loading.value = false }
  }
  onScopeDispose(() => { revision++; controller?.abort() })
  return { items, loading, error, nextCursor, query, framework, state, load }
}
