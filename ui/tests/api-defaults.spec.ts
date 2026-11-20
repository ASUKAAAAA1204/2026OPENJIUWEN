import { effectScope } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import { useApprovals } from '../src/components/workbench/approvals'
import { useDebts } from '../src/components/workbench/debts'
import { useEvaluations } from '../src/components/workbench/evaluations'
import { useSettings } from '../src/components/workbench/settings'

afterEach(() => vi.unstubAllGlobals())

it('正式入口默认请求 API，服务失败时不展示本地样例', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: false, status: 403 })
  vi.stubGlobal('fetch', fetcher)
  const scope = effectScope()
  const resources = scope.run(() => ({ approvals: useApprovals(), debts: useDebts(), evaluations: useEvaluations(), settings: useSettings() }))!
  try {
    await flushPromises()
    for (const resource of Object.values(resources)) {
      expect(resource.mode.value).toBe('api')
      expect(resource.error.value).not.toBe('')
    }
    expect(resources.approvals.items.value).toEqual([])
    expect(resources.debts.data.value).toBeNull()
    expect(resources.evaluations.data.value).toBeNull()
    expect(resources.settings.data.value).toBeNull()
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(expect.arrayContaining(['/api/v27/approvals', '/api/v27/debts', '/api/v27/evaluations', '/api/v27/settings']))
  } finally { scope.stop() }
})
