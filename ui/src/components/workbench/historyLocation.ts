export interface HistoryLocation { source: 'preview' | 'api'; framework: string; state: string }
const frameworks = ['全部框架', 'openJiuwen', 'LangGraph', 'OpenAI Agents SDK']
const states = ['全部状态', 'open', 'closed', 'degraded']
export function readHistoryLocation(hash: string): HistoryLocation | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  if (params.get('section') !== '运行记录' || !['preview', 'api'].includes(params.get('run_source') ?? '')) return null
  const framework = params.get('run_framework') ?? '', state = params.get('run_state') ?? ''
  return { source: params.get('run_source') as HistoryLocation['source'], framework: frameworks.includes(framework) ? framework : frameworks[0]!, state: states.includes(state) ? state : states[0]! }
}
export function writeHistoryLocation(value: HistoryLocation) {
  const params = new URLSearchParams({ section: '运行记录', run_source: value.source === 'api' ? 'api' : 'preview' })
  if (frameworks.includes(value.framework) && value.framework !== frameworks[0]) params.set('run_framework', value.framework)
  if (states.includes(value.state) && value.state !== states[0]) params.set('run_state', value.state)
  return `#${params}`
}
export function replaceHistoryLocation(value: HistoryLocation) {
  if (new URLSearchParams(location.hash.slice(1)).get('section') !== '运行记录') return
  history.replaceState(null, '', `${location.pathname}${location.search}${writeHistoryLocation(value)}`)
}
