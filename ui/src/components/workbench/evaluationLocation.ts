export function readEvaluationLocation(hash: string) {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  if (params.get('section') !== '版本评估') return null
  const source = params.get('evaluation_source')
  function identifier(key: string, fallback = '') {
    const value = params.get(key) ?? fallback
    return value === value.trim() && value.length <= 2048 ? value : fallback
  }
  return { source: source === 'api' || source === 'preview' ? source as 'api' | 'preview' : null,
    selected: identifier('evaluation_version'), scope: identifier('evaluation_scope', 'aggregate'),
    baseline: identifier('evaluation_baseline'), candidate: identifier('evaluation_candidate') }
}
export function writeEvaluationLocation(value: { source: 'api' | 'preview'; selected: string; scope: string; baseline: string; candidate: string }) {
  const params = new URLSearchParams({ section: '版本评估', evaluation_source: value.source })
  for (const [key, item] of Object.entries({ evaluation_version: value.selected, evaluation_scope: value.scope, evaluation_baseline: value.baseline, evaluation_candidate: value.candidate })) {
    if (item && item === item.trim() && item.length <= 2048) params.set(key, item)
  }
  return `#${params}`
}
